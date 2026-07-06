"""iCloud Hide My Email management APIs.

This segment is loaded after the source/account/mail helper segments and reuses
their globals from ``web_outlook_app.py``.
"""


ICLOUD_HME_LONG_RUNNER_LOCK = threading.Lock()
ICLOUD_HME_LONG_RUNNER_THREAD = None
ICLOUD_HME_LONG_RUNNER_STOP = threading.Event()
ICLOUD_HME_LONG_RUNNER_PAYLOADS = {}


def ensure_icloud_hme_management_runtime_columns(db=None) -> None:
    db = db or get_db()

    address_columns = {
        row['name']
        for row in db.execute('PRAGMA table_info(icloud_hme_address_cache)').fetchall()
    }
    if 'anonymous_id' not in address_columns:
        db.execute("ALTER TABLE icloud_hme_address_cache ADD COLUMN anonymous_id TEXT DEFAULT ''")

    candidate_columns = {
        row['name']
        for row in db.execute('PRAGMA table_info(icloud_hme_deactivation_candidates)').fetchall()
    }
    if 'deleted_at' not in candidate_columns:
        db.execute("ALTER TABLE icloud_hme_deactivation_candidates ADD COLUMN deleted_at TIMESTAMP")

    db.commit()


def normalize_bool_arg(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def normalize_int_arg(value, default=None, minimum=None, maximum=None):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and normalized < minimum:
        normalized = minimum
    if maximum is not None and normalized > maximum:
        normalized = maximum
    return normalized


def normalize_source_id(value) -> int:
    source_id = normalize_int_arg(value)
    if not source_id or source_id <= 0:
        raise ValueError('source_id 必须为正整数')
    return source_id


def normalize_hme_address_list_filters(args) -> Dict[str, Any]:
    return {
        'source_id': normalize_source_id(args.get('source_id')),
        'refresh': normalize_bool_arg(args.get('refresh'), False),
        'keyword': str(args.get('keyword') or args.get('q') or '').strip().lower(),
        'import_state': str(args.get('import_state') or args.get('state') or '').strip().lower(),
        'group_id': normalize_int_arg(args.get('group_id')),
        'active': str(args.get('active') or '').strip().lower(),
        'limit': normalize_int_arg(args.get('limit'), 500, 1, 5000),
        'offset': normalize_int_arg(args.get('offset'), 0, 0, 1000000),
    }


def normalize_hme_address(value) -> str:
    return normalize_email_address(str(value or '').strip())


def get_nested_value(payload, paths):
    if not isinstance(payload, dict):
        return None
    for path in paths:
        current = payload
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current.get(key)
        if found and current not in (None, ''):
            return current
    return None


def extract_hme_item_fields(item) -> Dict[str, Any]:
    if isinstance(item, str):
        hme = normalize_hme_address(item)
        return {
            'hme': hme,
            'label': '',
            'note': '',
            'is_active': True,
            'anonymous_id': '',
        }

    if not isinstance(item, dict):
        return {
            'hme': '',
            'label': '',
            'note': '',
            'is_active': False,
            'anonymous_id': '',
        }

    hme = normalize_hme_address(get_nested_value(item, [
        ('hme',),
        ('email',),
        ('address',),
        ('hmeEmail',),
        ('hideMyEmail',),
    ]))
    raw_status = str(item.get('status') or item.get('state') or '').strip().lower()
    is_active = item.get('isActive', item.get('active', None))
    if is_active is None:
        is_active = raw_status not in {'inactive', 'deactivated', 'deleted', 'disabled'}

    return {
        'hme': hme,
        'label': str(item.get('label') or item.get('name') or '').strip(),
        'note': str(item.get('note') or item.get('description') or '').strip(),
        'is_active': bool(is_active),
        'anonymous_id': str(
            item.get('anonymousId')
            or item.get('anonymous_id')
            or item.get('id')
            or ''
        ).strip(),
    }


def upsert_icloud_hme_address_cache(source_id, hme_items) -> None:
    ensure_icloud_hme_management_runtime_columns()
    db = get_db()
    rows = []
    seen = set()
    for item in hme_items or []:
        fields = extract_hme_item_fields(item)
        hme = fields.get('hme') or ''
        if not hme or hme in seen:
            continue
        seen.add(hme)
        rows.append((
            int(source_id),
            hme,
            fields.get('label') or '',
            fields.get('note') or '',
            'active' if fields.get('is_active') else 'inactive',
            fields.get('anonymous_id') or '',
        ))

    if not rows:
        return

    db.executemany(
        '''
        INSERT INTO icloud_hme_address_cache (
            source_id, hme, label, note, status, anonymous_id, last_seen_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(source_id, hme) DO UPDATE SET
            label = excluded.label,
            note = excluded.note,
            status = excluded.status,
            anonymous_id = excluded.anonymous_id,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        ''',
        rows,
    )
    db.commit()


def load_cached_icloud_hme_addresses(source_id) -> List[Dict[str, Any]]:
    ensure_icloud_hme_management_runtime_columns()
    db = get_db()
    rows = db.execute(
        '''
        SELECT hme, label, note, status, anonymous_id, last_seen_at, created_at, updated_at
        FROM icloud_hme_address_cache
        WHERE source_id = ?
        ORDER BY updated_at DESC, id DESC
        ''',
        (int(source_id),),
    ).fetchall()
    return [
        {
            'hme': row['hme'],
            'label': row['label'] or '',
            'note': row['note'] or '',
            'is_active': (row['status'] or 'active') == 'active',
            'anonymous_id': row['anonymous_id'] or '',
            'last_seen_at': row['last_seen_at'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }
        for row in rows
    ]


def build_icloud_hme_import_status_map(source_id, addresses) -> Dict[str, Dict[str, Any]]:
    normalized_addresses = []
    seen = set()
    for address in addresses or []:
        normalized = normalize_hme_address(address)
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_addresses.append(normalized)

    status_map = {
        address: {
            'account_id': None,
            'group_id': None,
            'group_name': None,
            'import_state': 'not_imported',
            'conflict': False,
        }
        for address in normalized_addresses
    }
    if not normalized_addresses:
        return status_map

    db = get_db()
    for chunk in chunk_account_ids(normalized_addresses):
        placeholders = ','.join('?' * len(chunk))
        rows = db.execute(
            f'''
            SELECT a.id, a.email, a.group_id, a.account_type, a.provider, a.icloud_hme_source_id,
                   g.name AS group_name
            FROM accounts a
            LEFT JOIN groups g ON g.id = a.group_id
            WHERE LOWER(a.email) IN ({placeholders})
            ''',
            tuple(chunk),
        ).fetchall()
        for row in rows:
            email_addr = normalize_hme_address(row['email'])
            existing_source_id = row['icloud_hme_source_id']
            same_source = (
                row['account_type'] == 'icloud_hme'
                and existing_source_id is not None
                and int(existing_source_id) == int(source_id)
            )
            status_map[email_addr] = {
                'account_id': int(row['id']),
                'group_id': row['group_id'],
                'group_name': row['group_name'] or '',
                'import_state': 'imported' if same_source else 'conflict',
                'conflict': not same_source,
            }
    return status_map


def list_icloud_hme_addresses(source_id, filters) -> Dict[str, Any]:
    source = get_icloud_hme_source_by_id(source_id, include_secret=bool(filters.get('refresh')))
    if not source:
        raise LookupError('iCloud HME 接收源不存在')

    refresh_error = ''
    if filters.get('refresh'):
        result = fetch_icloud_hme_list(
            source.get('cookie') or '',
            source.get('region') or 'global',
            source.get('maildomain_host') or '',
        )
        if not result.get('success'):
            refresh_error = sanitize_error_details(str(result.get('error') or '刷新 iCloud HME 列表失败'))
        else:
            upsert_icloud_hme_address_cache(source_id, result.get('hmeEmails') or [])

    items = load_cached_icloud_hme_addresses(source_id)
    status_map = build_icloud_hme_import_status_map(source_id, [item['hme'] for item in items])
    merged_items = []
    for item in items:
        state = status_map.get(item['hme'], {})
        merged = {
            'hme': item['hme'],
            'label': item.get('label') or '',
            'is_active': bool(item.get('is_active')),
            'anonymous_id': item.get('anonymous_id') or '',
            'account_id': state.get('account_id'),
            'group_id': state.get('group_id'),
            'group_name': state.get('group_name') or '',
            'import_state': state.get('import_state') or 'not_imported',
            'conflict': bool(state.get('conflict')),
        }
        merged_items.append(merged)

    keyword = filters.get('keyword') or ''
    if keyword:
        merged_items = [
            item for item in merged_items
            if keyword in item['hme'].lower() or keyword in (item.get('label') or '').lower()
        ]

    import_state = filters.get('import_state') or ''
    if import_state in {'imported', 'conflict', 'not_imported'}:
        merged_items = [item for item in merged_items if item['import_state'] == import_state]

    group_id = filters.get('group_id')
    if group_id and group_id > 0:
        merged_items = [
            item for item in merged_items
            if item.get('group_id') is not None and int(item.get('group_id')) == int(group_id)
        ]

    active = filters.get('active') or ''
    if active in {'1', 'true', 'active'}:
        merged_items = [item for item in merged_items if item['is_active']]
    elif active in {'0', 'false', 'inactive'}:
        merged_items = [item for item in merged_items if not item['is_active']]

    limit = filters.get('limit') or 500
    offset = filters.get('offset') or 0
    counts = {
        'total': len(items),
        'filtered': len(merged_items),
        'imported': sum(1 for item in merged_items if item['import_state'] == 'imported'),
        'conflict': sum(1 for item in merged_items if item['import_state'] == 'conflict'),
        'not_imported': sum(1 for item in merged_items if item['import_state'] == 'not_imported'),
    }
    summary = {
        'imported': counts['imported'],
        'conflict': counts['conflict'],
        'not_imported': counts['not_imported'],
    }

    return {
        'success': True,
        'source_id': int(source_id),
        'items': merged_items[offset:offset + limit],
        'counts': counts,
        'summary': summary,
        'pagination': {
            'total': len(merged_items),
            'limit': limit,
            'offset': offset,
        },
        'refresh_error': refresh_error,
    }


def import_icloud_hme_address_selection(source_id, group_id, addresses, remark='', status='active'):
    source_id = normalize_source_id(source_id)
    if not get_icloud_hme_source_by_id(source_id):
        raise LookupError('iCloud HME 接收源不存在')
    group_error = validate_account_target_group_id(group_id)
    if group_error:
        raise ValueError(group_error)

    results = []
    imported = []
    updated = []
    conflicts = []
    errors = []
    for raw_item in addresses or []:
        fields = extract_hme_item_fields(raw_item)
        hme = fields.get('hme') or ''
        if not hme:
            entry = {'hme': '', 'state': 'error', 'status': 'error', 'error': 'HME 邮箱格式无效'}
            results.append(entry)
            errors.append(entry)
            continue

        item_remark = str(remark or fields.get('note') or fields.get('label') or '').strip()
        result = add_icloud_hme_account(hme, source_id, group_id, remark=item_remark, status=status)
        if result.get('success'):
            state = 'updated' if result.get('updated') else 'imported'
            entry = {
                'hme': hme,
                'state': state,
                'status': state,
                'account_id': result.get('account_id'),
            }
            if state == 'updated':
                updated.append(entry)
            else:
                imported.append(entry)
            results.append(entry)
        elif result.get('conflict'):
            entry = {
                'hme': hme,
                'state': 'conflict',
                'status': 'conflict',
                'existing_account_id': result.get('existing_account_id'),
                'existing_source_id': result.get('existing_source_id'),
            }
            results.append(entry)
            conflicts.append(entry)
        else:
            entry = {
                'hme': hme,
                'state': 'error',
                'status': 'error',
                'error': sanitize_error_details(str(result.get('error') or '导入失败')),
            }
            results.append(entry)
            errors.append(entry)

    return {
        'success': True,
        'source_id': source_id,
        'group_id': group_id,
        'results': results,
        'imported': imported,
        'updated': updated,
        'conflicts': conflicts,
        'errors': errors,
        'imported_count': len(imported),
        'updated_count': len(updated),
        'conflict_count': len(conflicts),
        'error_count': len(errors),
    }


def get_running_icloud_hme_generation_task(db=None):
    db = db or get_db()
    return db.execute(
        '''
        SELECT *
        FROM icloud_hme_generation_tasks
        WHERE status IN ('pending', 'running', 'stopping')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        '''
    ).fetchone()


def serialize_icloud_hme_generation_task(row) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        'id': int(row['id']),
        'source_id': row['source_id'],
        'batch_id': row['batch_id'] or '',
        'status': row['status'] or 'pending',
        'total_requested': int(row['total_requested'] or 0),
        'generated_count': int(row['generated_count'] or 0),
        'success_count': int(row['success_count'] or 0),
        'failed_count': int(row['failed_count'] or 0),
        'failure_count': int(row['failed_count'] or 0),
        'duplicate_count': int(row['duplicate_count'] or 0),
        'stop_requested': bool(row['stop_requested']),
        'last_error': row['last_error'] or '',
        'started_at': row['started_at'],
        'stopped_at': row['stopped_at'],
        'finished_at': row['finished_at'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def create_icloud_hme_generation_task(payload) -> Dict[str, Any]:
    data = payload or {}
    source_id = normalize_source_id(data.get('source_id'))
    if not get_icloud_hme_source_by_id(source_id):
        raise LookupError('iCloud HME 接收源不存在')

    target_group_id = data.get('target_group_id', data.get('group_id', 1))
    group_error = validate_account_target_group_id(target_group_id)
    if group_error:
        raise ValueError(group_error)

    total_requested = normalize_int_arg(
        data.get('target_count', data.get('total_requested', data.get('count', data.get('total')))),
        1,
        None,
        10000,
    )
    if total_requested <= 0:
        raise ValueError('target_count 必须为正整数')

    success_delay_seconds = normalize_int_arg(data.get('success_delay_seconds'), 3, None, 3600)
    if success_delay_seconds < 0:
        raise ValueError('success_delay_seconds 不能为负数')
    failure_delay_seconds = normalize_int_arg(data.get('failure_delay_seconds'), 10, None, 3600)
    if failure_delay_seconds < 0:
        raise ValueError('failure_delay_seconds 不能为负数')

    runtime_payload = {
        'source_id': source_id,
        'target_group_id': int(target_group_id),
        'total_requested': total_requested,
        'label_prefix': str(data.get('label_prefix') or data.get('label') or 'OutlookEmail').strip(),
        'note': str(data.get('note') or data.get('remark') or '').strip(),
        'success_delay_seconds': success_delay_seconds,
        'failure_delay_seconds': failure_delay_seconds,
    }

    db = get_db()
    if get_running_icloud_hme_generation_task(db):
        raise RuntimeError('已有 HME 注册任务正在运行')

    cursor = db.execute(
        '''
        INSERT INTO icloud_hme_generation_tasks (
            source_id, batch_id, status, total_requested, updated_at
        )
        VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP)
        ''',
        (source_id, uuid.uuid4().hex, total_requested),
    )
    task_id = int(cursor.lastrowid)
    db.commit()
    ICLOUD_HME_LONG_RUNNER_PAYLOADS[task_id] = runtime_payload
    append_icloud_hme_generation_log(task_id, 'info', 'HME 长时注册任务已创建')
    return serialize_icloud_hme_generation_task(
        db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (task_id,)).fetchone()
    )


def append_icloud_hme_generation_log(task_id, level, message):
    db = get_db()
    db.execute(
        '''
        INSERT INTO icloud_hme_generation_logs (task_id, level, message)
        VALUES (?, ?, ?)
        ''',
        (int(task_id), str(level or 'info')[:20], sanitize_error_details(str(message or ''))[:1000]),
    )
    db.commit()


def extract_generated_hme_from_response(response) -> str:
    if not isinstance(response, dict):
        return ''
    value = get_nested_value(response, [
        ('result', 'hme'),
        ('data', 'result', 'hme'),
        ('data', 'hme'),
        ('hme',),
    ])
    return normalize_hme_address(value)


def wait_icloud_hme_long_runner_delay(seconds) -> bool:
    for _ in range(max(0, int(seconds or 0))):
        if ICLOUD_HME_LONG_RUNNER_STOP.wait(timeout=1):
            return False
    return True


def update_icloud_hme_task_counter(task_id, **updates):
    if not updates:
        return
    allowed = {
        'generated_count',
        'success_count',
        'failed_count',
        'duplicate_count',
        'last_error',
        'status',
        'stop_requested',
    }
    fields = []
    params = []
    for key, value in updates.items():
        if key not in allowed:
            continue
        fields.append(f'{key} = ?')
        params.append(value)
    if not fields:
        return
    fields.append('updated_at = CURRENT_TIMESTAMP')
    params.append(int(task_id))
    db = get_db()
    db.execute(
        f"UPDATE icloud_hme_generation_tasks SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )
    db.commit()


def run_icloud_hme_generation_task(task_id):
    with app.app_context():
        db = get_db()
        payload = ICLOUD_HME_LONG_RUNNER_PAYLOADS.get(int(task_id), {})
        task = db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (task_id,)).fetchone()
        if not task:
            return

        source_id = int(payload.get('source_id') or task['source_id'])
        source = get_icloud_hme_source_by_id(source_id, include_secret=True)
        if not source:
            update_icloud_hme_task_counter(
                task_id,
                status='failed',
                last_error='iCloud HME 接收源不存在',
            )
            append_icloud_hme_generation_log(task_id, 'error', 'iCloud HME 接收源不存在')
            return

        db.execute(
            '''
            UPDATE icloud_hme_generation_tasks
            SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (task_id,),
        )
        db.commit()
        append_icloud_hme_generation_log(task_id, 'info', 'HME 长时注册任务开始运行')

        total_requested = int(payload.get('total_requested') or task['total_requested'] or 0)
        target_group_id = int(payload.get('target_group_id') or 1)
        label_prefix = str(payload.get('label_prefix') or 'OutlookEmail').strip() or 'OutlookEmail'
        note = str(payload.get('note') or '').strip()
        success_delay = int(payload.get('success_delay_seconds') or 0)
        failure_delay = int(payload.get('failure_delay_seconds') or 0)

        stopped = False
        for index in range(1, total_requested + 1):
            current = db.execute(
                'SELECT status, stop_requested FROM icloud_hme_generation_tasks WHERE id = ?',
                (task_id,),
            ).fetchone()
            if ICLOUD_HME_LONG_RUNNER_STOP.is_set() or not current or current['stop_requested']:
                stopped = True
                break

            hme = ''
            label = f'{label_prefix}-{index}'
            try:
                generate_result = generate_icloud_hme(
                    source.get('cookie') or '',
                    source.get('region') or 'global',
                    source.get('maildomain_host') or '',
                )
                if not generate_result.get('success'):
                    raise RuntimeError(generate_result.get('error') or '生成 HME 地址失败')

                hme = extract_generated_hme_from_response(generate_result)
                if not hme:
                    raise RuntimeError('生成响应中未包含 HME 地址')

                reserve_result = reserve_icloud_hme(
                    source.get('cookie') or '',
                    source.get('region') or 'global',
                    source.get('maildomain_host') or '',
                    hme,
                    label,
                    note,
                )
                if not reserve_result.get('success'):
                    raise RuntimeError(reserve_result.get('error') or '保留 HME 地址失败')

                account_result = add_icloud_hme_account(
                    hme,
                    source_id,
                    target_group_id,
                    remark=note,
                    status='active',
                )
                if not account_result.get('success'):
                    if account_result.get('conflict'):
                        db.execute(
                            '''
                            INSERT INTO icloud_hme_generated_addresses (
                                task_id, source_id, hme, label, note, status, account_id,
                                error_message, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, 'duplicate', ?, ?, CURRENT_TIMESTAMP)
                            ''',
                            (
                                task_id,
                                source_id,
                                hme,
                                label,
                                note,
                                account_result.get('existing_account_id'),
                                '账号已存在',
                            ),
                        )
                        db.commit()
                        update_icloud_hme_task_counter(task_id, generated_count=index, duplicate_count=task['duplicate_count'] + 1)
                        append_icloud_hme_generation_log(task_id, 'warning', f'{hme} 已存在，跳过导入')
                        if not wait_icloud_hme_long_runner_delay(success_delay):
                            stopped = True
                            break
                        task = db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (task_id,)).fetchone()
                        continue
                    raise RuntimeError(account_result.get('error') or '导入 HME 账号失败')

                db.execute(
                    '''
                    INSERT INTO icloud_hme_generated_addresses (
                        task_id, source_id, hme, label, note, status, account_id, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'imported', ?, CURRENT_TIMESTAMP)
                    ''',
                    (task_id, source_id, hme, label, note, account_result.get('account_id')),
                )
                db.execute(
                    '''
                    INSERT INTO icloud_hme_address_cache (
                        source_id, hme, label, note, status, last_seen_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(source_id, hme) DO UPDATE SET
                        label = excluded.label,
                        note = excluded.note,
                        status = 'active',
                        last_seen_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    ''',
                    (source_id, hme, label, note),
                )
                db.commit()
                update_icloud_hme_task_counter(task_id, generated_count=index, success_count=task['success_count'] + 1)
                append_icloud_hme_generation_log(task_id, 'info', f'{hme} 已生成、保留并导入')
                if not wait_icloud_hme_long_runner_delay(success_delay):
                    stopped = True
                    break
            except Exception as exc:
                error_message = sanitize_error_details(str(exc))[:500]
                db.execute(
                    '''
                    INSERT INTO icloud_hme_generated_addresses (
                        task_id, source_id, hme, label, note, status, error_message,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'error', ?, CURRENT_TIMESTAMP)
                    ''',
                    (task_id, source_id, hme or '', label, note, error_message),
                )
                db.commit()
                update_icloud_hme_task_counter(
                    task_id,
                    generated_count=index,
                    failed_count=task['failed_count'] + 1,
                    last_error=error_message,
                )
                append_icloud_hme_generation_log(task_id, 'error', error_message)
                if not wait_icloud_hme_long_runner_delay(failure_delay):
                    stopped = True
                    break

            task = db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (task_id,)).fetchone()

        final_status = 'stopped' if stopped else 'completed'
        db.execute(
            f'''
            UPDATE icloud_hme_generation_tasks
            SET status = ?, stop_requested = CASE WHEN ? = 'stopped' THEN 1 ELSE stop_requested END,
                stopped_at = CASE WHEN ? = 'stopped' THEN CURRENT_TIMESTAMP ELSE stopped_at END,
                finished_at = CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE finished_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (final_status, final_status, final_status, final_status, task_id),
        )
        db.commit()
        append_icloud_hme_generation_log(
            task_id,
            'info',
            'HME 长时注册任务已停止' if stopped else 'HME 长时注册任务已完成',
        )


def request_stop_icloud_hme_generation_task(task_id=None):
    db = get_db()
    params = []
    where = "status IN ('pending', 'running', 'stopping')"
    if task_id is not None:
        where += ' AND id = ?'
        params.append(int(task_id))
    task = db.execute(
        f'SELECT * FROM icloud_hme_generation_tasks WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT 1',
        tuple(params),
    ).fetchone()
    if not task:
        return None

    ICLOUD_HME_LONG_RUNNER_STOP.set()
    db.execute(
        '''
        UPDATE icloud_hme_generation_tasks
        SET stop_requested = 1, status = 'stopping', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (int(task['id']),),
    )
    db.commit()
    append_icloud_hme_generation_log(int(task['id']), 'info', '已请求停止 HME 长时注册任务')
    return serialize_icloud_hme_generation_task(
        db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (int(task['id']),)).fetchone()
    )


def refresh_icloud_hme_address_cache_if_needed(source, refresh=False):
    if not refresh:
        return
    result = fetch_icloud_hme_list(
        source.get('cookie') or '',
        source.get('region') or 'global',
        source.get('maildomain_host') or '',
    )
    if result.get('success'):
        upsert_icloud_hme_address_cache(source['id'], result.get('hmeEmails') or [])


def scan_icloud_hme_deactivation_candidates(
    source_id,
    group_id=None,
    folder='all',
    subject_contains='OpenAI - Access Deactivated',
    limit=200,
    refresh=False,
):
    ensure_icloud_hme_management_runtime_columns()
    source_id = normalize_source_id(source_id)
    source = get_icloud_hme_source_by_id(source_id, include_secret=bool(refresh))
    if not source:
        raise LookupError('iCloud HME 接收源不存在')
    refresh_icloud_hme_address_cache_if_needed(source, refresh=refresh)

    params = [source_id, f"%{str(subject_contains or '').strip()}%"]
    folder_clause = ''
    if str(folder or 'all').lower() != 'all':
        folder_clause = 'AND m.folder = ?'
        params.append(str(folder).strip())

    group_clause = ''
    if group_id not in (None, ''):
        group_error = validate_account_target_group_id(group_id)
        if group_error:
            raise ValueError(group_error)
        group_clause = 'AND a.group_id = ?'
        params.append(int(group_id))

    params.append(normalize_int_arg(limit, 200, 1, 5000))
    db = get_db()
    rows = db.execute(
        f'''
        SELECT LOWER(r.hme_address) AS hme, MIN(m.subject) AS subject,
               MIN(m.received_at) AS received_at, MIN(a.id) AS account_id,
               MIN(a.group_id) AS group_id, MIN(g.name) AS group_name,
               MIN(cache.anonymous_id) AS anonymous_id
        FROM icloud_hme_source_messages m
        JOIN icloud_hme_source_message_recipients r ON r.source_message_id = m.id
        LEFT JOIN accounts a
          ON LOWER(a.email) = LOWER(r.hme_address)
         AND a.account_type = 'icloud_hme'
         AND a.icloud_hme_source_id = m.source_id
        LEFT JOIN groups g ON g.id = a.group_id
        LEFT JOIN icloud_hme_address_cache cache
          ON cache.source_id = m.source_id
         AND LOWER(cache.hme) = LOWER(r.hme_address)
        WHERE m.source_id = ?
          AND m.subject LIKE ?
          {folder_clause}
          {group_clause}
        GROUP BY LOWER(r.hme_address)
        ORDER BY MAX(m.received_at_sort) DESC, hme ASC
        LIMIT ?
        ''',
        tuple(params),
    ).fetchall()

    candidates = []
    for row in rows:
        hme = normalize_hme_address(row['hme'])
        if not hme:
            continue
        reason = str(row['subject'] or subject_contains or 'OpenAI - Access Deactivated')
        db.execute(
            '''
            INSERT INTO icloud_hme_deactivation_candidates (
                source_id, hme, account_id, reason, status, updated_at
            )
            VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            ON CONFLICT(source_id, hme) DO UPDATE SET
                account_id = excluded.account_id,
                reason = excluded.reason,
                status = CASE
                    WHEN icloud_hme_deactivation_candidates.status = 'deleted' THEN 'deleted'
                    ELSE 'pending'
                END,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (source_id, hme, row['account_id'], reason),
        )
        candidates.append({
            'hme': hme,
            'account_id': row['account_id'],
            'group_id': row['group_id'],
            'group_name': row['group_name'] or '',
            'anonymous_id': row['anonymous_id'] or '',
            'reason': reason,
        })
    db.commit()

    return {
        'success': True,
        'source_id': source_id,
        'scanned_count': len(rows),
        'candidate_count': len(candidates),
        'candidates': candidates,
    }


def list_icloud_hme_deactivation_candidates(source_id, status=None, limit=200):
    ensure_icloud_hme_management_runtime_columns()
    source_id = normalize_source_id(source_id)
    if not get_icloud_hme_source_by_id(source_id):
        raise LookupError('iCloud HME 接收源不存在')

    params = [source_id]
    status_clause = ''
    if status:
        status_clause = 'AND c.status = ?'
        params.append(str(status).strip())
    params.append(normalize_int_arg(limit, 200, 1, 5000))

    rows = get_db().execute(
        f'''
        SELECT c.*, cache.anonymous_id, a.group_id, g.name AS group_name
        FROM icloud_hme_deactivation_candidates c
        LEFT JOIN icloud_hme_address_cache cache
          ON cache.source_id = c.source_id AND LOWER(cache.hme) = LOWER(c.hme)
        LEFT JOIN accounts a ON a.id = c.account_id
        LEFT JOIN groups g ON g.id = a.group_id
        WHERE c.source_id = ?
          {status_clause}
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        ''',
        tuple(params),
    ).fetchall()

    return [
        {
            'id': int(row['id']),
            'source_id': int(row['source_id']),
            'hme': row['hme'],
            'account_id': row['account_id'],
            'group_id': row['group_id'],
            'group_name': row['group_name'] or '',
            'anonymous_id': row['anonymous_id'] or '',
            'reason': row['reason'] or '',
            'status': row['status'] or 'pending',
            'error': row['last_error'] or '',
            'detected_at': row['detected_at'],
            'deactivated_at': row['deactivated_at'],
            'deleted_at': row['deleted_at'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }
        for row in rows
    ]


def hme_api_result_is_success_or_already_done(result, keywords):
    if result.get('success'):
        return True
    error = str(result.get('error') or '').lower()
    return any(keyword in error for keyword in keywords)


def append_hme_deleted_remark(remark: str) -> str:
    marker = f'HME deleted at {datetime.utcnow().isoformat(timespec="seconds")}Z'
    current = str(remark or '').strip()
    if marker in current:
        return current
    return f'{current}\n{marker}'.strip() if current else marker


def find_icloud_hme_anonymous_id(source_id, hme) -> str:
    row = get_db().execute(
        '''
        SELECT anonymous_id
        FROM icloud_hme_address_cache
        WHERE source_id = ? AND LOWER(hme) = LOWER(?)
        LIMIT 1
        ''',
        (source_id, hme),
    ).fetchone()
    return (row['anonymous_id'] or '').strip() if row else ''


def delete_icloud_hme_deactivation_candidates(source_id, candidate_ids):
    ensure_icloud_hme_management_runtime_columns()
    source_id = normalize_source_id(source_id)
    source = get_icloud_hme_source_by_id(source_id, include_secret=True)
    if not source:
        raise LookupError('iCloud HME 接收源不存在')

    normalized_ids = []
    for candidate_id in candidate_ids or []:
        normalized = normalize_int_arg(candidate_id)
        if normalized and normalized > 0 and normalized not in normalized_ids:
            normalized_ids.append(normalized)
    if not normalized_ids:
        raise ValueError('请选择要删除的候选项')

    placeholders = ','.join('?' * len(normalized_ids))
    db = get_db()
    rows = db.execute(
        f'''
        SELECT *
        FROM icloud_hme_deactivation_candidates
        WHERE source_id = ? AND id IN ({placeholders})
        ORDER BY id ASC
        ''',
        tuple([source_id] + normalized_ids),
    ).fetchall()
    found_ids = {int(row['id']) for row in rows}
    missing_ids = [candidate_id for candidate_id in normalized_ids if candidate_id not in found_ids]
    if missing_ids:
        raise LookupError('候选项不存在')

    if any(not find_icloud_hme_anonymous_id(source_id, row['hme']) for row in rows):
        refresh_icloud_hme_address_cache_if_needed(source, refresh=True)

    results = []
    for row in rows:
        candidate_id = int(row['id'])
        hme = row['hme']
        anonymous_id = find_icloud_hme_anonymous_id(source_id, hme)
        if not anonymous_id:
            error_message = '未找到 HME anonymousId，无法删除'
            db.execute(
                '''
                UPDATE icloud_hme_deactivation_candidates
                SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (error_message, candidate_id),
            )
            db.commit()
            results.append({'id': candidate_id, 'hme': hme, 'state': 'failed', 'error': error_message})
            continue

        try:
            deactivate_result = deactivate_icloud_hme(
                source.get('cookie') or '',
                source.get('region') or 'global',
                source.get('maildomain_host') or '',
                anonymous_id,
            )
            if not hme_api_result_is_success_or_already_done(
                deactivate_result,
                {'already', 'deactivated', 'inactive', 'disabled', 'not active', '已停用', '不存在'},
            ):
                raise RuntimeError(deactivate_result.get('error') or '停用 HME 地址失败')

            db.execute(
                '''
                UPDATE icloud_hme_deactivation_candidates
                SET status = 'deactivated', deactivated_at = COALESCE(deactivated_at, CURRENT_TIMESTAMP),
                    last_error = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (candidate_id,),
            )
            db.commit()

            delete_result = delete_icloud_hme(
                source.get('cookie') or '',
                source.get('region') or 'global',
                source.get('maildomain_host') or '',
                anonymous_id,
            )
            if not hme_api_result_is_success_or_already_done(
                delete_result,
                {'already', 'deleted', 'not found', '不存在', '已删除'},
            ):
                raise RuntimeError(delete_result.get('error') or '删除 HME 地址失败')

            db.execute(
                '''
                UPDATE icloud_hme_deactivation_candidates
                SET status = 'deleted', deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                    last_error = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (candidate_id,),
            )
            db.execute(
                '''
                UPDATE icloud_hme_address_cache
                SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
                WHERE source_id = ? AND LOWER(hme) = LOWER(?)
                ''',
                (source_id, hme),
            )
            account_id = row['account_id']
            if account_id:
                account_row = db.execute(
                    'SELECT remark FROM accounts WHERE id = ?',
                    (account_id,),
                ).fetchone()
                db.execute(
                    '''
                    UPDATE accounts
                    SET status = 'inactive', remark = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (append_hme_deleted_remark(account_row['remark'] if account_row else ''), account_id),
                )
            db.commit()
            results.append({'id': candidate_id, 'hme': hme, 'state': 'deleted'})
        except Exception as exc:
            error_message = sanitize_error_details(str(exc))[:500]
            db.execute(
                '''
                UPDATE icloud_hme_deactivation_candidates
                SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (error_message, candidate_id),
            )
            db.commit()
            results.append({'id': candidate_id, 'hme': hme, 'state': 'failed', 'error': error_message})

    return {
        'success': True,
        'source_id': source_id,
        'results': results,
        'deleted_count': sum(1 for item in results if item.get('state') == 'deleted'),
        'error_count': sum(1 for item in results if item.get('state') == 'failed'),
    }


@app.route('/api/icloud-hme/addresses', methods=['GET'])
@login_required
def api_get_icloud_hme_addresses():
    try:
        filters = normalize_hme_address_list_filters(request.args)
        return jsonify(list_icloud_hme_addresses(filters['source_id'], filters))
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '获取 iCloud HME 地址列表失败'}), 500


@app.route('/api/icloud-hme/addresses/import', methods=['POST'])
@login_required
def api_import_icloud_hme_addresses():
    data = request.get_json(silent=True) or {}
    try:
        result = import_icloud_hme_address_selection(
            data.get('source_id'),
            data.get('group_id', data.get('target_group_id', 1)),
            data.get('addresses') or data.get('items') or [],
            remark=str(data.get('remark') or '').strip(),
            status=data.get('status', 'active'),
        )
        return jsonify(result)
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '导入 iCloud HME 地址失败'}), 500


@app.route('/api/icloud-hme/long-runner/status', methods=['GET'])
@login_required
def api_get_icloud_hme_long_runner_status():
    db = get_db()
    task_id = normalize_int_arg(request.args.get('task_id'))
    if task_id:
        row = db.execute('SELECT * FROM icloud_hme_generation_tasks WHERE id = ?', (task_id,)).fetchone()
    else:
        row = get_running_icloud_hme_generation_task(db) or db.execute(
            '''
            SELECT *
            FROM icloud_hme_generation_tasks
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            '''
        ).fetchone()
    return jsonify({
        'success': True,
        'task': serialize_icloud_hme_generation_task(row) if row else None,
        'thread_alive': bool(ICLOUD_HME_LONG_RUNNER_THREAD and ICLOUD_HME_LONG_RUNNER_THREAD.is_alive()),
    })


@app.route('/api/icloud-hme/long-runner/start', methods=['POST'])
@login_required
def api_start_icloud_hme_long_runner():
    global ICLOUD_HME_LONG_RUNNER_THREAD
    data = request.get_json(silent=True) or {}
    try:
        with ICLOUD_HME_LONG_RUNNER_LOCK:
            if get_running_icloud_hme_generation_task():
                return jsonify({'success': False, 'error': '已有 HME 注册任务正在运行'}), 409
            task = create_icloud_hme_generation_task(data)
            ICLOUD_HME_LONG_RUNNER_STOP.clear()
            ICLOUD_HME_LONG_RUNNER_THREAD = threading.Thread(
                target=run_icloud_hme_generation_task,
                args=(task['id'],),
                name=f"icloud-hme-long-runner-{task['id']}",
                daemon=True,
            )
            ICLOUD_HME_LONG_RUNNER_THREAD.start()
        return jsonify({'success': True, 'task': task}), 202
    except RuntimeError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '启动 HME 长时注册任务失败'}), 500


@app.route('/api/icloud-hme/long-runner/stop', methods=['POST'])
@login_required
def api_stop_icloud_hme_long_runner():
    data = request.get_json(silent=True) or {}
    try:
        task = request_stop_icloud_hme_generation_task(data.get('task_id'))
        if not task:
            return jsonify({'success': False, 'error': '未找到运行中的 HME 长时注册任务'}), 404
        return jsonify({'success': True, 'task': task})
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '停止 HME 长时注册任务失败'}), 500


@app.route('/api/icloud-hme/long-runner/logs', methods=['GET'])
@login_required
def api_get_icloud_hme_long_runner_logs():
    task_id = normalize_int_arg(request.args.get('task_id'))
    limit = normalize_int_arg(request.args.get('limit'), 200, 1, 1000)
    db = get_db()
    if not task_id:
        row = db.execute(
            '''
            SELECT id
            FROM icloud_hme_generation_tasks
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            '''
        ).fetchone()
        task_id = int(row['id']) if row else None
    if not task_id:
        return jsonify({'success': True, 'logs': []})
    rows = db.execute(
        '''
        SELECT id, task_id, level, message, details_json, created_at
        FROM icloud_hme_generation_logs
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (task_id, limit),
    ).fetchall()
    logs = [
        {
            'id': int(row['id']),
            'task_id': int(row['task_id']),
            'level': row['level'],
            'message': row['message'],
            'details_json': row['details_json'] or '',
            'created_at': row['created_at'],
        }
        for row in reversed(rows)
    ]
    return jsonify({'success': True, 'task_id': task_id, 'logs': logs})


@app.route('/api/icloud-hme/deactivation-candidates/scan', methods=['POST'])
@login_required
def api_scan_icloud_hme_deactivation_candidates():
    data = request.get_json(silent=True) or {}
    try:
        result = scan_icloud_hme_deactivation_candidates(
            data.get('source_id'),
            group_id=data.get('group_id'),
            folder=data.get('folder', 'all'),
            subject_contains=data.get('subject_contains', 'OpenAI - Access Deactivated'),
            limit=data.get('limit', 200),
            refresh=normalize_bool_arg(data.get('refresh'), False),
        )
        return jsonify(result)
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '扫描 HME 停用候选失败'}), 500


@app.route('/api/icloud-hme/deactivation-candidates', methods=['GET'])
@login_required
def api_get_icloud_hme_deactivation_candidates():
    try:
        source_id = normalize_source_id(request.args.get('source_id'))
        status = str(request.args.get('status') or '').strip() or None
        limit = normalize_int_arg(request.args.get('limit'), 200, 1, 1000)
        return jsonify({
            'success': True,
            'source_id': source_id,
            'candidates': list_icloud_hme_deactivation_candidates(source_id, status=status, limit=limit),
        })
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '获取 HME 停用候选失败'}), 500


@app.route('/api/icloud-hme/deactivation-candidates/delete', methods=['POST'])
@login_required
def api_delete_icloud_hme_deactivation_candidates():
    data = request.get_json(silent=True) or {}
    try:
        result = delete_icloud_hme_deactivation_candidates(
            data.get('source_id'),
            data.get('candidate_ids') or data.get('ids') or [],
        )
        return jsonify(result)
    except LookupError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': '删除 HME 停用候选失败'}), 500
