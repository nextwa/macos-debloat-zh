#!/usr/bin/env python3
"""Measure an application-cache reclaim request and subsequent memory changes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import debloat
import memory_tools as memory


def write_report(directory: Path, control: dict, result: dict, observations: list[tuple[str, dict]]) -> None:
    before = result['before']
    samples = [('对照起点', control), ('回收前', before), *observations]
    lines = ['# 应用缓存回收实验', '',
             f"时间：{control['recorded_at']} → {samples[-1][1]['recorded_at']}",
             '回收方式：1 秒 warn 级别模拟内存压力通知。', '',
             '| 指标 | ' + ' | '.join(name for name, _ in samples) + ' |',
             '|---|' + '---:|' * len(samples)]
    for label, key in memory.METRICS:
        lines.append('| ' + label + ' | ' + ' | '.join(memory.mib(s['memory'][key]) for _, s in samples) + ' |')
    lines += ['| 内存压力 | ' + ' | '.join(s['memory']['pressure'] for _, s in samples) + ' |',
              '| 进程数 | ' + ' | '.join(str(s['process_count']) for _, s in samples) + ' |', '',
              '## 回收后的变化', '',
              '| 采样 | 压缩器变化 | 同批进程足迹变化 | 比较进程数 | 交换读入 | 交换写出 |',
              '|---|---:|---:|---:|---:|---:|']
    for name, snapshot in observations:
        delta = memory.compare(before, snapshot)
        lines.append(f"| {name} | {memory.mib(delta['memory_delta']['compressor_bytes'], signed=True)} | "
                     f"{memory.mib(delta['stable_footprint_delta_bytes'], signed=True)} | {delta['stable_process_count']} | "
                     f"{memory.mib(delta['swapin_bytes'])} | {memory.mib(delta['swapout_bytes'])} |")
    control_delta = memory.compare(control, before)
    lines += ['', f"回收前对照间隔内，同批进程足迹变化 {memory.mib(control_delta['stable_footprint_delta_bytes'], signed=True)}，"
              f"压缩器变化 {memory.mib(control_delta['memory_delta']['compressor_bytes'], signed=True)}。", '',
              '## 最后一次应用/进程组采样', '', '| 应用/进程组 | 内存足迹 | RSS | 进程数 |', '|---|---:|---:|---:|']
    for row in samples[-1][1]['groups'][:20]:
        lines.append(f"| {row['name']} | {memory.footprint_text(row)} | {memory.mib(row['rss_bytes'])} | {row['process_count']} |")
    lines += ['', '## 测量范围', '',
              '- 同批进程按 PID 和进程启动时间匹配，仅比较两次都能读取的内存足迹。',
              '- 足迹使用系统计费口径；RSS 包含共享页。组内不可读的进程用 ≥ 标识，不当作零占用。',
              '- 前台任务和后台活动会影响采样差值；本实验不测任务速度，也不能证明长期收益。',
              '- 缓存回收请求成功不表示所有应用释放了内存；文件缓存与空闲页变化单独展示。', '']
    (directory / 'comparison.md').write_text('\n'.join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not debloat.prime_sudo():
        print('管理员认证未完成。', file=sys.stderr)
        return 2
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    control = memory.capture()
    memory.write_json(directory / 'control-start.json', control)
    print('对照起点已记录；10 秒后记录基线并发送回收通知。', flush=True)
    time.sleep(10)
    before = memory.capture()
    memory.write_json(directory / 'before.json', before)
    result = memory.reclaim(debloat.BACKUP_DIR, before=before)
    memory.write_json(directory / 'reclaim.json', result)
    print('\n'.join(memory.report_lines(result)), flush=True)
    if result['status'] != 'completed':
        return 2
    observations = [('约 3 秒', result['after'])]
    memory.write_json(directory / 'after-3s.json', result['after'])
    origin = time.monotonic() - 3
    for seconds in (30, 120):
        remaining = origin + seconds - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        snapshot = memory.capture()
        observations.append((f'约 {seconds} 秒', snapshot))
        memory.write_json(directory / f'after-{seconds}s.json', snapshot)
        delta = memory.compare(before, snapshot)
        print(json.dumps({'after_seconds': seconds, 'memory': snapshot['memory'],
                          'stable_footprint_delta_bytes': delta['stable_footprint_delta_bytes'],
                          'swapout_bytes': delta['swapout_bytes']}, ensure_ascii=False), flush=True)
    write_report(directory, control, result, observations)
    print(f'完整报告：{directory / "comparison.md"}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
