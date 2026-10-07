#!/usr/bin/env python3
"""Capture comparable macOS memory, process and launchd observations."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone, timedelta
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('debloat', ROOT / 'debloat.py')
debloat = importlib.util.module_from_spec(spec)
sys.modules['debloat'] = debloat
spec.loader.exec_module(debloat)


def command(*argv: str) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f'{argv[0]}: {result.stderr.strip()}')
    return result.stdout.strip()


def processes() -> dict[int, dict]:
    found = {}
    for line in command('ps', '-axo', 'pid=,ppid=,rss=,pcpu=,comm=').splitlines():
        fields = line.split(None, 4)
        if len(fields) != 5:
            continue
        pid, parent, rss, cpu, executable = fields
        found[int(pid)] = {'pid': int(pid), 'parent_pid': int(parent),
                           'rss_bytes': int(rss) * 1024, 'cpu_pct': float(cpu),
                           'name': Path(executable).name}
    return found


def quantity(value: str, unit: str) -> int:
    return int(float(value) * 1024 ** {'B': 0, 'K': 1, 'M': 2, 'G': 3, 'T': 4}[unit])


def capture(destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    top = command('top', '-l', '2', '-s', '2', '-n', '0')
    vm_text = command('vm_stat')
    page_size = int(re.search(r'page size of (\d+) bytes', vm_text).group(1))
    vm = {key.strip('"'): int(value) for key, value in re.findall(r'^([^:]+):\s+(\d+)\.', vm_text, re.M)}
    swap_text = command('sysctl', 'vm.swapusage')
    swap_value, swap_unit = re.search(r'used = ([\d.]+)([BKMGT])', swap_text).groups()
    pressure = command('memory_pressure', '-Q')
    available = re.search(r'System-wide memory free percentage:\s+(\d+)%', pressure)
    cpu = re.findall(r'CPU usage: ([\d.]+)% user, ([\d.]+)% sys, ([\d.]+)% idle', top)[-1]
    ram = int(command('sysctl', '-n', 'hw.memsize'))
    procs = processes()
    sections, _, absent, version_skip = debloat.load_sections()
    domains = {domain: debloat.domain_state(domain) for domain in debloat.DOMAINS}
    services = []
    for sec in sections:
        for item in sec.items:
            pids = sorted({domains[d][0].get(item.label, 0) for d in item.domains} - {0})
            services.append({
                'label': item.label, 'section': sec.title,
                'domains': sorted(item.domains),
                'disabled_domains': sorted(d for d in item.domains if item.label in domains[d][1]),
                'disabled': all(item.label in domains[d][1] for d in item.domains),
                'running': bool(pids), 'pids': pids,
                'rss_bytes': sum(procs.get(pid, {}).get('rss_bytes', 0) for pid in pids),
                'cpu_pct': sum(procs.get(pid, {}).get('cpu_pct', 0) for pid in pids),
                'keep_reason': debloat.preserve_reason(item.label),
                'sip_tag': item.is_sip_off_required,
            })
    targets = [row for row in services if not row['keep_reason']]
    free_bytes = (vm.get('Pages free', 0) + vm.get('Pages speculative', 0)) * page_size
    observation = {
        'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'os': command('sw_vers', '-productVersion'), 'build': command('sw_vers', '-buildVersion'),
        'architecture': command('uname', '-m'), 'physical_memory_bytes': ram,
        'sip_enabled': debloat.is_sip_enabled(), 'spotlight': debloat.spotlight_state(),
        'memory': {
            'physical_nonfree_bytes': ram - free_bytes, 'free_and_speculative_bytes': free_bytes,
            'compressor_bytes': vm.get('Pages occupied by compressor', 0) * page_size,
            'wired_bytes': vm.get('Pages wired down', 0) * page_size,
            'file_backed_bytes': vm.get('File-backed pages', 0) * page_size,
            'swap_used_bytes': quantity(swap_value, swap_unit),
            'memory_pressure_free_pct': int(available.group(1)) if available else None,
        },
        'cpu': dict(zip(('user_pct', 'system_pct', 'idle_pct'), map(float, cpu))),
        'summary': {
            'process_count': len(procs), 'catalog_present': len(services),
            'target_count': len(targets), 'target_running': sum(row['running'] for row in targets),
            'target_disabled': sum(row['disabled'] for row in targets),
            'target_disabled_but_running': sum(row['disabled'] and row['running'] for row in targets),
            'target_rss_bytes': sum(row['rss_bytes'] for row in targets),
            'target_cpu_pct': sum(row['cpu_pct'] for row in targets),
        },
        'processes': sorted(procs.values(), key=lambda row: -row['rss_bytes']),
        'services': services, 'absent_labels': absent, 'version_skipped_labels': version_skip,
        'raw': {'top': top, 'vm_stat': vm_text, 'swapusage': swap_text, 'memory_pressure': pressure},
    }
    destination.write_text(json.dumps(observation, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'file': str(destination), 'summary': observation['summary'],
                      'memory': observation['memory']}, ensure_ascii=False), flush=True)
    return observation


def mib(value: float) -> str:
    return f'{value / 1024**2:,.1f} MiB'


def compare(before: dict, after: dict, destination: Path) -> None:
    before_labels = {row['label']: row for row in before['services']}
    after_labels = {row['label']: row for row in after['services']}
    targets = [label for label, row in before_labels.items() if not row['keep_reason']]
    stopped = [label for label in targets if before_labels[label]['running'] and not after_labels.get(label, {}).get('running', False)]
    new_running = [label for label in targets if not before_labels[label]['running'] and after_labels.get(label, {}).get('running', False)]
    survivors = [row for row in after['services'] if not row['keep_reason'] and row['running']]
    delta = {key: after['memory'][key] - before['memory'][key] for key in before['memory']
             if before['memory'][key] is not None and after['memory'][key] is not None}
    result = {'before': before['recorded_at'], 'after': after['recorded_at'],
              'memory_delta': delta, 'stopped_labels': stopped, 'new_running_labels': new_running,
              'still_running': survivors,
              'summary_delta': {key: after['summary'][key] - value for key, value in before['summary'].items()}}
    lines = [
        '# macOS 精简实验对比', '',
        f"系统：macOS {before['os']} ({before['build']})，{before['architecture']}，物理内存 {mib(before['physical_memory_bytes'])}。",
        f"记录时间：{before['recorded_at']} → {after['recorded_at']}（UTC+08:00）。",
        f"SIP：{'开启' if after['sip_enabled'] else '关闭'}；Spotlight：{after['spotlight']}。", '',
        '| 指标 | 执行前 | 执行后 | 变化 |', '|---|---:|---:|---:|',
    ]
    metrics = [('物理非空闲内存', 'physical_nonfree_bytes'), ('空闲与推测页', 'free_and_speculative_bytes'),
               ('压缩器实际占用', 'compressor_bytes'), ('Wired 内存', 'wired_bytes'),
               ('文件缓存页', 'file_backed_bytes'), ('交换空间占用', 'swap_used_bytes')]
    for label, key in metrics:
        lines.append(f"| {label} | {mib(before['memory'][key])} | {mib(after['memory'][key])} | {mib(delta[key])} |")
    key = 'memory_pressure_free_pct'
    lines.append(f"| memory_pressure 可用比例 | {before['memory'][key]}% | {after['memory'][key]}% | {delta.get(key, 0):+d} 个百分点 |")
    for label, key in [('全机进程数', 'process_count'), ('目标服务运行数', 'target_running'),
                       ('目标服务禁用标记数', 'target_disabled'), ('禁用但仍运行', 'target_disabled_but_running')]:
        lines.append(f"| {label} | {before['summary'][key]} | {after['summary'][key]} | {result['summary_delta'][key]:+d} |")
    key = 'target_rss_bytes'
    lines.append(f"| 目标服务 RSS 合计 | {mib(before['summary'][key])} | {mib(after['summary'][key])} | {mib(result['summary_delta'][key])} |")
    bcpu = before['cpu']['user_pct'] + before['cpu']['system_pct']
    acpu = after['cpu']['user_pct'] + after['cpu']['system_pct']
    lines.append(f'| 全机 CPU 忙碌比例 | {bcpu:.2f}% | {acpu:.2f}% | {acpu-bcpu:+.2f} 个百分点 |')
    lines += ['', f'执行前正在运行、执行后已停止的目标服务：{len(stopped)} 个；新启动目标：{len(new_running)} 个。',
              '', '## 仍在运行的目标服务', '', '| 服务 | RSS | 禁用标记 |', '|---|---:|---|']
    for row in sorted(survivors, key=lambda row: -row['rss_bytes']):
        lines.append(f"| `{row['label']}` | {mib(row['rss_bytes'])} | {'已写入' if row['disabled'] else '未生效'} |")
    lines += ['', '## 保留项目', '', '| 服务 | 用途 | 禁用标记 | 运行状态 |', '|---|---|---|---|']
    for row in after['services']:
        if row['keep_reason']:
            lines.append(f"| `{row['label']}` | {row['keep_reason']} | {'仍被禁用' if row['disabled'] else '允许启动'} | {'运行' if row['running'] else '未运行/按需启动'} |")
    lines += ['', '## 全机内存占用前 20 个进程', '', '| 进程 | RSS | CPU |', '|---|---:|---:|']
    for row in after['processes'][:20]:
        lines.append(f"| `{row['name']}` | {mib(row['rss_bytes'])} | {row['cpu_pct']:.1f}% |")
    lines += ['', '## 测量限制', '',
              '- 物理非空闲内存 = 总内存 − 空闲页 − 推测页，包含可回收缓存，不等同于活动监视器的应用内存。',
              '- RSS 包含共享页；RSS 合计下降不等于独占物理内存释放量。进程 CPU 为 ps 采样值，全机 CPU 为 top 第二次采样。',
              '- 两次记录之间的前台应用、索引和缓存活动会影响整机数据；服务状态、目标进程数和目标 RSS 共同用于判断效果。',
              '- 基线是执行前的现有配置，可能已包含禁用项目。短时记录不证明重启后持续生效。',
              '- AirDrop 传输和应用搜索交互需要另行验证。', '']
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('\n'.join(lines))
    destination.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def run_experiment(output: Path, attempt_protected: bool) -> int:
    if not debloat.prime_sudo():
        return 1
    output.mkdir(parents=True, exist_ok=True)
    print('采集实验前状态', flush=True)
    before = capture(output / 'before.json')
    sections = debloat.load_sections()[0]
    debloat.lock_sip_rows(sections, debloat.is_sip_enabled() and not attempt_protected)
    with (output / 'apply.log').open('w') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = debloat.cmd_preset(sections, debloat.EXTREME_PRESET, dry_run=False)
    (output / 'apply-result.json').write_text(json.dumps({'exit_code': code}, indent=2))
    snapshot = debloat.BACKUP_DIR / 'latest.json'
    if snapshot.exists():
        (output / 'restore-snapshot.json').write_text(snapshot.read_text())
    if code not in (0, 2):
        return code
    start = time.monotonic()
    for seconds in (30, 90):
        remaining = seconds - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(remaining)
        print(f'采集执行后 {seconds} 秒状态', flush=True)
        after = capture(output / f'after-{seconds}s.json')
        compare(before, after, output / f'comparison-{seconds}s.md')
    print(f'实验完成，记录位于 {output}', flush=True)
    if os.geteuid() == 0 and debloat.UID:
        import pwd
        gid = pwd.getpwuid(debloat.UID).pw_gid
        for path in [output, *output.rglob('*')]:
            os.chown(path, debloat.UID, gid)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('capture', 'run'):
        sub = commands.add_parser(action)
        sub.add_argument('--output', type=Path, required=True)
        if action == 'run':
            sub.add_argument('--attempt-protected', action='store_true')
    sub = commands.add_parser('compare')
    sub.add_argument('before', type=Path)
    sub.add_argument('after', type=Path)
    sub.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'capture':
        capture(args.output)
    elif args.command == 'compare':
        compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()), args.output)
    else:
        return run_experiment(args.output, args.attempt_protected)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
