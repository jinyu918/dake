"""安装当前项目的本地后台用户服务；不读取或复制应用密钥。"""

from pathlib import Path
import subprocess


def main():
    """注册自动重启与登录启动；首次启动前须先退出占用端口的旧后端。"""
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv/bin/python"
    if not python.exists():
        raise SystemExit("请先安装项目 Python 环境")
    if any(char in str(root) for char in ('\n', '\r', '"', '%')):
        raise SystemExit("项目路径含不支持的服务配置字符")
    folder = Path.home() / ".config/systemd/user"
    folder.mkdir(parents=True, exist_ok=True)
    unit = folder / "dake-aftersales-backend.service"
    content = f'''[Unit]
Description=大客售后本地后台与定时任务
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={root}
ExecStart="{python}" -m uvicorn dake.main:app --host 127.0.0.1 --port 8001 --no-access-log
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
TimeoutStopSec=180
UMask=0077

[Install]
WantedBy=default.target
'''
    if unit.exists() and unit.read_text() != content:
        raise SystemExit("已有不同的同名服务，请先核对，未覆盖")
    unit.write_text(content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", unit.name], check=True)
    print("本地后台服务已注册。退出旧后端后运行 systemctl --user start dake-aftersales-backend")


if __name__ == "__main__":
    main()
