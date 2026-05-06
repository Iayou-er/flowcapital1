#!/usr/bin/env python3
"""
经济新闻分析系统启动脚本
"""

import os
import sys
import subprocess
import threading
import signal

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)

# 全局控制
_scheduler_process = None
_api_process = None
_scheduler_restart_count = 0
_scheduler_should_run = True
_MAX_RESTART = 5
_RESTART_DELAYS = [10, 30, 60, 120, 300]  # 指数退避（秒）


def ensure_directories():
    for directory in ['data', 'logs']:
        path = os.path.join(PROJECT_ROOT, directory)
        os.makedirs(path, exist_ok=True)


def get_env():
    env = os.environ.copy()
    env['PYTHONPATH'] = PROJECT_ROOT
    return env


def run_crawl_once():
    """立即执行一次爬虫 + 分析（不启动定时调度）"""
    print("正在执行一次性爬虫...\n")
    try:
        # 使用 subprocess 运行 scheduler 的 crawl_and_analyze_news 函数
        code = """
import sys, os
sys.path.insert(0, {root!r})
os.chdir({root!r})

from scripts.scheduler import crawl_and_analyze_news
crawl_and_analyze_news()
print('\\n一次性爬虫执行完成')
""".format(root=PROJECT_ROOT)

        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=PROJECT_ROOT,
            env=get_env(),
            text=True,
            capture_output=True,
        )
        print(result.stdout)
        if result.stderr:
            print("[stderr]", result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
        if result.returncode == 0:
            print("\n一次性爬虫执行完成")
        else:
            print(f"\n爬虫执行异常 (exit code: {result.returncode})")
    except Exception as e:
        print(f"运行爬虫出错: {e}")


def _start_scheduler_process():
    """启动调度器子进程"""
    global _scheduler_process
    proc = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPTS_DIR, 'scheduler.py')],
        cwd=PROJECT_ROOT,
        env=get_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 日志输出线程
    def log_reader():
        try:
            for line in proc.stdout:
                print(f"[scheduler] {line}", end='')
        except Exception:
            pass

    t = threading.Thread(target=log_reader, daemon=True)
    t.start()

    return proc


def run_scheduler():
    """后台运行调度器（崩溃自动重启）"""
    global _scheduler_process, _scheduler_restart_count, _scheduler_should_run
    _scheduler_should_run = True
    _scheduler_restart_count = 0

    print("正在启动调度器（每15分钟自动爬取，崩溃自动重启）...")

    def monitor():
        global _scheduler_process, _scheduler_restart_count, _scheduler_should_run
        while _scheduler_should_run:
            try:
                _scheduler_process = _start_scheduler_process()
                print(f"调度器已启动 (PID: {_scheduler_process.pid})")

                # 等待子进程结束
                exit_code = _scheduler_process.wait()

                if not _scheduler_should_run:
                    break

                _scheduler_restart_count += 1
                # 如果连续正常运行超过 30 分钟，重置计数器
                if exit_code == 0:
                    import time as _t
                    _t.sleep(1800)  # 等待30分钟再检查
                    if _scheduler_should_run:
                        _scheduler_restart_count = 0
                if _scheduler_restart_count > _MAX_RESTART:
                    print(f"调度器已崩溃 {_scheduler_restart_count} 次，超过最大重启次数，停止重试")
                    break

                delay = _RESTART_DELAYS[min(_scheduler_restart_count - 1, len(_RESTART_DELAYS) - 1)]
                print(f"调度器已退出 (exit={exit_code})，{delay}s 后第 {_scheduler_restart_count} 次重启...")
                import time
                time.sleep(delay)

            except Exception as e:
                print(f"调度器监控异常: {e}")
                break

    t = threading.Thread(target=monitor, daemon=True)
    t.start()


def stop_scheduler():
    global _scheduler_process, _scheduler_should_run
    _scheduler_should_run = False
    if _scheduler_process and _scheduler_process.poll() is None:
        print("正在停止调度器...")
        _scheduler_process.send_signal(signal.SIGTERM)
        try:
            _scheduler_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _scheduler_process.kill()
            _scheduler_process.wait()
        print("调度器已停止")


def run_api():
    """前台运行 API 服务"""
    print("正在启动 API 服务 (http://0.0.0.0:8001) ...")
    print("按 Ctrl+C 停止\n")
    try:
        cmd = [sys.executable, '-m', 'uvicorn', 'backend.api.main:app',
               '--host', '0.0.0.0', '--port', '8001']
        # Windows 下 multiprocessing 用 spawn 而非 fork，多 worker 会各自 bind 端口导致冲突
        if os.name != 'nt':
            cmd += ['--workers', '4']
        else:
            print("[Windows] 单 worker 模式（平台限制）\n")
        subprocess.run(cmd, cwd=PROJECT_ROOT, env=get_env())
    except KeyboardInterrupt:
        print("\nAPI 服务已停止")
    except Exception as e:
        print(f"API 服务出错: {e}")


def run_scheduler_and_api():
    """同时运行调度器和 API"""
    run_scheduler()
    try:
        run_api()
    finally:
        stop_scheduler()


def main():
    # 信号处理
    def handle_exit(sig, frame):
        stop_scheduler()
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    print("经济新闻分析系统启动脚本")
    print("=" * 30)
    print(f"项目根目录: {PROJECT_ROOT}")
    ensure_directories()

    print("\n1. 立即运行一次爬虫")
    print("2. 启动 API 服务")
    print("3. 同时启动爬虫调度器 + API 服务")
    print("4. 仅启动爬虫调度器")

    choice = input("\n请选择 (1-4): ").strip()

    if choice == '1':
        run_crawl_once()
    elif choice == '2':
        run_api()
    elif choice == '3':
        print("同时启动爬虫调度器和 API 服务...\n")
        run_scheduler_and_api()
    elif choice == '4':
        print("仅启动爬虫调度器（后台）...\n")
        run_scheduler()
        print("按 Ctrl+C 停止\n")
        try:
            signal.pause()
        except AttributeError:
            while True:
                try:
                    import time
                    time.sleep(1)
                except KeyboardInterrupt:
                    break
        stop_scheduler()
    else:
        print("无效选择")


if __name__ == "__main__":
    main()
