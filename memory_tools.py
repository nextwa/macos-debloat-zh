"""Observe macOS memory and request cooperative application cache reclamation."""
from __future__ import annotations

import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

MIB = 1024 ** 2
PRESSURE_NAMES = {1: "正常", 2: "偏高", 4: "紧张"}
RECLAIM_COMMAND = ["/usr/bin/memory_pressure", "-S", "-l", "warn", "-s", "1"]
METRICS = (
    ("压缩器实际占用", "compressor_bytes"),
    ("Wired 内存", "wired_bytes"),
    ("文件缓存页", "file_backed_bytes"),
    ("空闲与推测页", "free_and_speculative_bytes"),
    ("交换空间占用", "swap_used_bytes"),
)


class RUsageInfo(ctypes.Structure):
    """rusage_info_v0 from the macOS sys/resource.h SDK header."""
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "idle_wakeups", "interrupt_wakeups", "pageins",
            "wired_size", "resident_size", "phys_footprint", "start_time", "exit_time",
        )
    ]


def command(*argv: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{argv[0]}：{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def parse_memory(vm_text: str, swap_text: str, total: int, pressure: int) -> dict:
    page_size = int(re.search(r"page size of (\d+) bytes", vm_text).group(1))
    pages = {k.strip('"'): int(v) for k, v in re.findall(r'^([^:\n]+):\s+(\d+)\.', vm_text, re.M)}
    swap, unit = re.search(r"used = ([\d.]+)([BKMGT])", swap_text).groups()
    return {
        "physical_memory_bytes": total,
        "pressure_level": pressure,
        "pressure": PRESSURE_NAMES.get(pressure, f"未知 ({pressure})"),
        "compressor_bytes": pages["Pages occupied by compressor"] * page_size,
        "wired_bytes": pages["Pages wired down"] * page_size,
        "file_backed_bytes": pages["File-backed pages"] * page_size,
        "free_and_speculative_bytes": (pages["Pages free"] + pages["Pages speculative"]) * page_size,
        "swap_used_bytes": int(float(swap) * 1024 ** "BKMGT".index(unit)),
        "swapin_bytes_total": pages["Swapins"] * page_size,
        "swapout_bytes_total": pages["Swapouts"] * page_size,
    }


def memory_state() -> dict:
    return parse_memory(command("/usr/bin/vm_stat"), command("/usr/sbin/sysctl", "vm.swapusage"),
                        int(command("/usr/sbin/sysctl", "-n", "hw.memsize")),
                        int(command("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level")))


def process_rows() -> list[dict]:
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    rows = []
    for line in command("/bin/ps", "-axo", "pid=,ppid=,rss=,comm=").splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            continue
        pid, parent, rss, executable = fields
        usage = RUsageInfo()
        measured = lib.proc_pid_rusage(int(pid), 0, ctypes.byref(usage)) == 0
        match = re.match(r"(.+?\.app)(?:/|$)", executable)
        rows.append({
            "pid": int(pid), "parent_pid": int(parent), "name": Path(executable).name,
            "bundle": match.group(1) if match else None,
            "rss_bytes": int(rss) * 1024,
            "footprint_bytes": usage.phys_footprint if measured else None,
            "start_time": usage.start_time if measured else None,
        })
    return rows


def group_processes(rows: list[dict]) -> list[dict]:
    by_pid = {row["pid"]: row for row in rows}
    groups = {}
    for row in rows:
        bundle = row["bundle"]
        parent = row["parent_pid"]
        seen = {row["pid"]}
        while not bundle and parent in by_pid and parent not in seen:
            seen.add(parent)
            ancestor = by_pid[parent]
            bundle, parent = ancestor["bundle"], ancestor["parent_pid"]
        key = bundle or "process:" + row["name"]
        group = groups.setdefault(key, {
            "id": key, "name": Path(bundle).stem if bundle else row["name"],
            "kind": "app" if bundle else "process", "pids": [], "rss_bytes": 0,
            "footprint_bytes": 0, "measured_count": 0,
        })
        group["pids"].append(row["pid"])
        group["rss_bytes"] += row["rss_bytes"]
        if row["footprint_bytes"] is not None:
            group["footprint_bytes"] += row["footprint_bytes"]
            group["measured_count"] += 1
    for group in groups.values():
        group["process_count"] = len(group["pids"])
        group["complete"] = group["measured_count"] == group["process_count"]
        if not group["measured_count"]:
            group["footprint_bytes"] = None
    return sorted(groups.values(), key=lambda row: -(row["footprint_bytes"] or 0))


def capture() -> dict:
    memory = memory_state()
    rows = process_rows()
    return {
        "recorded_at": datetime.now().astimezone().isoformat(),
        "memory": memory, "process_count": len(rows),
        "measured_count": sum(row["footprint_bytes"] is not None for row in rows),
        "groups": group_processes(rows), "processes": rows,
    }


def reclaim_plan(snapshot: dict) -> dict:
    level = snapshot["memory"]["pressure_level"]
    if level not in (1, 2):
        return {"allowed": False, "reason": "当前内存压力紧张或无法识别，请先处理占用较高的应用。"}
    return {"allowed": True,
            "reason": "当前压力正常，回收收益可能很小。" if level == 1 else "当前压力偏高，可请求应用释放可丢弃缓存。",
            "command": RECLAIM_COMMAND.copy()}


def compare(before: dict, after: dict) -> dict:
    def measured(snapshot):
        return {(p["pid"], p["start_time"]): p for p in snapshot["processes"]
                if p["footprint_bytes"] is not None}

    old, new = measured(before), measured(after)
    common = old.keys() & new.keys()
    footprint_delta = sum(new[key]["footprint_bytes"] - old[key]["footprint_bytes"] for key in common)
    old_groups = {row["id"]: row for row in before["groups"]}
    group_deltas = []
    for row in after["groups"]:
        prior = old_groups.get(row["id"])
        if not prior or not prior["complete"] or not row["complete"]:
            continue
        group_deltas.append({"name": row["name"], "before_bytes": prior["footprint_bytes"],
                             "after_bytes": row["footprint_bytes"],
                             "delta_bytes": row["footprint_bytes"] - prior["footprint_bytes"],
                             "before_processes": prior["process_count"], "after_processes": row["process_count"]})
    return {
        "memory_delta": {key: after["memory"][key] - before["memory"][key] for _, key in METRICS},
        "swapin_bytes": after["memory"]["swapin_bytes_total"] - before["memory"]["swapin_bytes_total"],
        "swapout_bytes": after["memory"]["swapout_bytes_total"] - before["memory"]["swapout_bytes_total"],
        "stable_process_count": len(common), "stable_footprint_delta_bytes": footprint_delta,
        "process_count_delta": after["process_count"] - before["process_count"],
        "groups": sorted(group_deltas, key=lambda row: row["delta_bytes"]),
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if os.geteuid() == 0 and "SUDO_UID" in os.environ:
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))


def reclaim(state_dir: Path, *, before: dict | None = None) -> dict:
    before = before or capture()
    plan = reclaim_plan(before)
    report = {"recorded_at": before["recorded_at"], "before": before, "plan": plan, "status": "skipped"}
    if plan["allowed"]:
        argv = RECLAIM_COMMAND if os.geteuid() == 0 else ["/usr/bin/sudo", "-n", *RECLAIM_COMMAND]
        # Let the one-second simulation restore the kernel's normal pressure handling.
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            result = subprocess.run(argv, text=True, capture_output=True, check=False)
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        report.update(returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())
        report["status"] = "completed" if result.returncode == 0 else "failed"
        if result.returncode == 0:
            time.sleep(3)
            report["after"] = capture()
            report["comparison"] = compare(before, report["after"])
    write_json(state_dir / "last-memory.json", report)
    return report


def mib(value: int, *, signed: bool = False) -> str:
    return f"{value / MIB:+,.1f} MiB" if signed else f"{value / MIB:,.1f} MiB"


def footprint_text(row: dict) -> str:
    if row["footprint_bytes"] is None:
        return "不可读"
    return ("" if row["complete"] else "≥") + mib(row["footprint_bytes"])


def snapshot_lines(snapshot: dict, limit: int = 20) -> list[str]:
    memory = snapshot["memory"]
    lines = [f"内存压力：{memory['pressure']}  |  物理内存：{mib(memory['physical_memory_bytes'])}",
             "  |  ".join(f"{label}：{mib(memory[key])}" for label, key in METRICS[:2]),
             "  |  ".join(f"{label}：{mib(memory[key])}" for label, key in METRICS[2:]),
             f"进程 {snapshot['process_count']} 个；已读取 {snapshot['measured_count']} 个进程的内存足迹。", "",
             "按应用与辅助进程合并（足迹 / RSS / 进程数）："]
    for row in snapshot["groups"][:limit]:
        lines.append(f"{row['name']}  {footprint_text(row)} / {mib(row['rss_bytes'])} / {row['process_count']}")
    lines += ["", "足迹使用系统计费口径；RSS 包含共享页，两者不混加。≥ 表示只读到部分进程。",
              "按应用包路径和父进程归组；无法归属的系统进程单列。"]
    return lines


def report_lines(report: dict) -> list[str]:
    if report["status"] == "skipped":
        return ["未执行回收：" + report["plan"]["reason"]]
    if report["status"] == "failed":
        return [f"回收请求失败（退出码 {report['returncode']}）。", report["stderr"] or report["stdout"]]
    before, after, delta = report["before"], report["after"], report["comparison"]
    lines = ["已发送一次应用缓存回收通知。", f"采样：{before['recorded_at']} → {after['recorded_at']}",
             f"内存压力：{before['memory']['pressure']} → {after['memory']['pressure']}", ""]
    for label, key in METRICS:
        lines.append(f"{label}：{mib(before['memory'][key])} → {mib(after['memory'][key])}（{mib(delta['memory_delta'][key], signed=True)}）")
    lines += ["", f"同一批 {delta['stable_process_count']} 个进程的足迹变化：{mib(delta['stable_footprint_delta_bytes'], signed=True)}",
              f"期间交换读入 {mib(delta['swapin_bytes'])}；交换写出 {mib(delta['swapout_bytes'])}；进程数变化 {delta['process_count_delta']:+d}。",
              "", "应用/进程组变化（采样差值）："]
    for row in sorted(delta["groups"], key=lambda r: -abs(r["delta_bytes"]))[:10]:
        lines.append(f"{row['name']}  {mib(row['delta_bytes'], signed=True)}  进程 {row['before_processes']} → {row['after_processes']}")
    lines += ["", "短时变化包含同期应用活动；文件缓存减少和通知成功均不等于持续性能提升。"]
    return lines
