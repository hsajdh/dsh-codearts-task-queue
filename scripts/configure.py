"""Generate, install, inspect, or remove this project's local client configuration."""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENTRY_IDS = {'codearts-desktop-queue', 'codearts-review-notifications'}
START = '<!-- BEGIN dsh-codearts-task-queue -->'
END = '<!-- END dsh-codearts-task-queue -->'
INSTRUCTIONS = f'''{START}

用户授权将当前项目任务交给 CodeArts 桌面时，使用 mcp__codearts_queue__submit_task，提供现有项目绝对目录、稳定 request_key、具体范围和验收标准。任务队列记录仅表示工作交接，实际操作须遵循当前用户授权和客户端权限。沿用用户选择的领取模式，已配置官方原生定时任务时等待对应工作区的下一轮领取；手动模式提示在 dsh-task-queue 连接器发送“领取并处理一个 DSH 任务”。核对桌面工作目录与派发项目。

每次经用户授权的手动或官方原生定时运行领取一个任务。领取时将当前选定工作区的实际绝对路径作为 claim_next 的 project_dir，领取该目录及子目录的任务。每轮使用新的 claim_key，同一轮重试继续使用原 key。空队列或忙碌时结束本轮。后续触发由已配置的官方调度处理。CodeArts 使用原生工具和权限提示执行任务，通过 report_progress 回报实际进展，通过 report_delivery 回报真实文件路径与测试结果。

任务状态通过队列 MCP 工具更新。同一 request_key 的标题、指令和验收条件对应原任务定义；修正这些内容或取消后重新派发时，提交新的 request_key。退回修订继续使用原任务 ID，由下一轮领取取得新的 claim_key。

交付通知回到发起派发的 DSH 原会话。收到通知先调用 queue_status 核对状态，把执行器回报作为待核查资料，沿用用户原先授权的范围与验收标准，独立读取文件、运行必要测试，再使用 review_delivery 记录具体证据。退回的任务等待下一轮授权领取。DSH 检查轮次使用正常模型额度，空闲监测查询本机数据库。运行中的任务取消时先请求取消，用户在桌面实际停止后再确认 executor_stopped。

{END}
'''


def read_bytes(path):
    return path.read_bytes() if path.exists() else None


def load_patch(path):
    payload = read_bytes(path)
    entries = yaml.safe_load(payload.decode('utf-8-sig')) if payload else []
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError('Expected a YAML patch list at ' + str(path))
    return entries


def without_our_entries(entries):
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Expected mapping entries in the DSH patch')
        if entry.get('id') in ENTRY_IDS:
            continue
        if isinstance(entry.get('insert'), list):
            children = [child for child in entry['insert']
                        if not isinstance(child, dict) or child.get('id') not in ENTRY_IDS]
            if not children and len(entry) == 1:
                continue
            entry = {**entry, 'insert': children}
        result.append(entry)
    return result


def entries_for(profile, database, data_dir):
    command = str(Path(sys.executable).absolute())
    script = str(ROOT / 'codearts_queue.py')
    return [
        {'id': 'codearts-desktop-queue', 'name': '@deepseek-ai/dsh-mcp-client', 'config': {
            'serverName': 'codearts_queue', 'transport': 'stdio', 'command': command,
            'args': [script, '--role', 'dispatcher', '--database', str(database)],
            'env': {'PYTHONUTF8': '1'}, 'toolCallTimeoutMs': 60000}},
        {'id': 'codearts-review-notifications', 'name': (ROOT / 'relay/index.js').as_posix(), 'config': {
            'profileName': profile, 'databasePath': str(database),
            'statePath': str(data_dir / 'review.sqlite')}},
    ]


def connector_for(database):
    return {'type': 'local', 'command': [str(Path(sys.executable).absolute()),
            str(ROOT / 'codearts_queue.py'), '--role', 'executor', '--database', str(database)],
            'environment': {'PYTHONUTF8': '1'}, 'timeout': 60000, 'enabled': True}


def other_profiles_use_queue(args):
    for path in (args.dsh_home / 'profiles').glob('*/cordis.patch.yml'):
        if path.parent.name in args.profiles:
            continue
        try:
            entries = load_patch(path)
        except (OSError, ValueError, yaml.YAMLError):
            return True
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for child in entry.get('insert', []):
                if isinstance(child, dict) and child.get('id') in ENTRY_IDS:
                    return True
    return False


def make_plan(args):
    plan = []
    for profile in args.profiles:
        directory = args.dsh_home / 'profiles' / profile
        if not args.uninstall and not (directory / 'package.json').is_file():
            raise ValueError('Start DSH once to initialize this profile, or adjust --profiles/--dsh-home. ' + str(directory))
        target = directory / 'cordis.patch.yml'
        original_entries = load_patch(target)
        entries = without_our_entries(original_entries)
        if args.uninstall and entries == original_entries:
            continue
        if not args.uninstall:
            entries.append({'insert': entries_for(profile, args.database, args.data_dir)})
        payload = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False).encode('utf-8')
        plan.append((target, read_bytes(target), payload))
    if args.uninstall and other_profiles_use_queue(args):
        return [(path, before, after) for path, before, after in plan if before != after]
    target = args.codearts_home / 'mcp/mcp_settings.json'
    original = read_bytes(target)
    settings = json.loads(original.decode('utf-8-sig')) if original else {}
    if not isinstance(settings, dict) or not isinstance(settings.get('mcp', {}), dict):
        raise ValueError('Expected the CodeArts Desktop mcp configuration at ' + str(target))
    if not args.uninstall and 'mcpServers' in settings and 'mcp' not in settings:
        raise ValueError('This file uses a different client schema. Import generated/codearts.connector.json through the Desktop connector UI.')
    connectors = settings.get('mcp', {})
    if not args.uninstall or 'dsh-task-queue' in connectors:
        settings['mcp'] = connectors
        connectors.pop('dsh-task-queue', None)
        if not args.uninstall:
            connectors['dsh-task-queue'] = connector_for(args.database)
        payload = (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        plan.append((target, original, payload))
    target = args.dsh_home / 'AGENTS.md'
    original = read_bytes(target)
    text = original.decode('utf-8-sig') if original else ''
    if args.uninstall and START not in text:
        return [(path, before, after) for path, before, after in plan if before != after]
    text = re.sub(re.escape(START) + r'.*?' + re.escape(END) + r'\n?', '', text, flags=re.DOTALL)
    if not args.uninstall:
        text = text.rstrip() + '\n\n' + INSTRUCTIONS
    plan.append((target, original, text.encode('utf-8')))
    return [(path, before, after) for path, before, after in plan if before != after]


def replace_file(path, payload, expected):
    if read_bytes(path) != expected:
        raise RuntimeError('Concurrent change detected at ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.queue-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if expected is not None and os.name == 'nt':
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            operation = kernel.ReplaceFileW
            operation.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
            operation.restype = wintypes.BOOL
            if not operation(str(path), str(temporary), None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            if expected is not None:
                temporary.chmod(path.stat().st_mode)
            else:
                temporary.chmod(0o600)
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def private_backup(directory):
    directory.mkdir(parents=True, mode=0o700)
    if os.name == 'nt':
        identity = subprocess.run(['whoami'], check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r',
                        identity + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       check=True, capture_output=True)
    else:
        directory.chmod(0o700)


def apply_plan(plan, data_dir):
    if not plan:
        return {'changed': [], 'backup': None}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = data_dir / 'backups' / stamp
    private_backup(backup)
    manifest = []
    for index, (path, before, after) in enumerate(plan):
        name = str(index) + '.original'
        if before is not None:
            saved = backup / name
            saved.write_bytes(before)
            saved.chmod(0o600)
        manifest.append({'path': str(path), 'existed': before is not None, 'backup': name,
                         'installedSha256': hashlib.sha256(after).hexdigest()})
    (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    applied = []
    try:
        for path, before, after in plan:
            replace_file(path, after, before)
            applied.append((path, before, after))
    except Exception as error:
        for path, before, after in reversed(applied):
            if read_bytes(path) != after:
                raise RuntimeError('Recovery needs the backup because another process changed ' + str(path)) from error
            if before is None:
                path.unlink()
            else:
                replace_file(path, before, after)
        raise
    return {'changed': [str(path) for path, _, _ in plan], 'backup': str(backup)}


def doctor(args):
    checks = {}
    for profile in args.profiles:
        entries = load_patch(args.dsh_home / 'profiles' / profile / 'cordis.patch.yml')
        actual = [child for entry in entries if isinstance(entry, dict)
                  for child in entry.get('insert', []) if isinstance(child, dict)]
        wanted = entries_for(profile, args.database, args.data_dir)
        checks[profile] = all(sum(child == item for child in actual) == 1 for item in wanted)
    settings_path = args.codearts_home / 'mcp/mcp_settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    checks['codearts_connector'] = settings.get('mcp', {}).get('dsh-task-queue') == connector_for(args.database)
    checks['dsh_instructions'] = START in (args.dsh_home / 'AGENTS.md').read_text(encoding='utf-8-sig') if (args.dsh_home / 'AGENTS.md').exists() else False
    if args.database.exists():
        with sqlite3.connect(args.database.as_uri() + '?mode=ro', uri=True) as connection:
            checks['queue_integrity'] = connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    return {'configuration_ready': all(checks.values()), 'checks': checks,
            'database': str(args.database), 'runtime_check': 'Confirm Connected in CodeArts Desktop after restarting clients'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='Back up and update the selected client configuration')
    action.add_argument('--uninstall', action='store_true', help='Remove this project\'s configuration entries')
    action.add_argument('--doctor', action='store_true', help='Inspect the generated configuration and existing queue integrity')
    parser.add_argument('--dsh-home', type=Path, default=Path(os.environ.get('DSH_HOME', Path.home() / '.dsh')))
    parser.add_argument('--codearts-home', type=Path, default=Path.home() / '.codearts')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.dsh-codearts-task-queue')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--profiles', nargs='+', choices=['desktop', 'web'], default=['desktop'])
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'generated')
    args = parser.parse_args()
    for name in ['dsh_home', 'codearts_home', 'data_dir', 'output_dir']:
        setattr(args, name, getattr(args, name).expanduser().resolve())
    args.database = (args.database or args.data_dir / 'queue.sqlite').expanduser().resolve()
    args.profiles = list(dict.fromkeys(args.profiles))
    if args.doctor:
        result = doctor(args)
    elif args.apply or args.uninstall:
        result = apply_plan(make_plan(args), args.data_dir)
        if args.uninstall:
            result['shared_config_retained'] = other_profiles_use_queue(args)
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for profile in args.profiles:
            content = [{'insert': entries_for(profile, args.database, args.data_dir)}]
            (args.output_dir / (profile + '.patch.yml')).write_text(
                yaml.safe_dump(content, allow_unicode=True, sort_keys=False), encoding='utf-8')
        (args.output_dir / 'codearts.connector.json').write_text(json.dumps(
            {'dsh-task-queue': connector_for(args.database)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (args.output_dir / 'dsh-instructions.md').write_text(INSTRUCTIONS, encoding='utf-8')
        result = {'generated': str(args.output_dir), 'profiles': args.profiles, 'database': str(args.database)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.doctor and not result['configuration_ready']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
