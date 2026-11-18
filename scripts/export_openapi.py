"""从后端代码导出接口定义；不启动应用生命周期、不读取聊天或连接数据库。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from dake.api.app_factory import create_app
from dake.config import Settings


def main() -> None:
    """只保存接口结构，前端据此生成客户端；禁止把真实响应当成接口示例。"""
    destination = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="dake-schema-empty-") as directory:
        # 只生成全部路由的结构；显式空依赖不创建生产仓库，也不执行应用生命周期。
        app = create_app(Settings(uid="schema-export", data_dir=Path(directory)), run_repository=object(), runner=object(),
                         query_service=object(), start_worker=False)
        destination.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("接口定义已更新：docs/api/openapi.json")


if __name__ == "__main__":
    main()
