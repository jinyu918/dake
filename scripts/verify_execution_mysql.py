"""使用显式指定的全新隔离库验收执行记录；不启动或重启任何服务。"""

import argparse
import os
from pathlib import Path
import sys
import tempfile


def parser():
    """连接参数全部必填，不加载应用配置或普通 MySQL 环境变量。"""
    result = argparse.ArgumentParser(description="过程记录隔离 MySQL 8.4 验收")
    result.add_argument("--host", required=True)
    result.add_argument("--port", required=True, type=int)
    result.add_argument("--database", required=True)
    result.add_argument("--user", required=True)
    result.add_argument("--password-file", required=True)
    result.add_argument("--suite", choices=("executions", "panel", "all"), default="executions")
    result.add_argument("--panel-manifest", help="仅全部测试通过后新建 /tmp 内的虚构面板案例索引，不保存密码或聊天")
    return result


def main(argv=None):
    """先保护连接与文件访问，再建测试表和运行固定的合成验收集合。"""
    args = parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "backend/src"), str(root / "backend/tests")]
    from mysql_acceptance import DatabasePlugin, TestDatabase, install_guard, prepare_database, read_test_password
    from dake.repositories.mysql import StorageError
    from panel_preview import validate_manifest_path, write_manifest

    try:
        # 先校验全部非敏感连接参数，再读取明确指定的临时测试密码。
        TestDatabase(args.host, args.port, args.database, args.user, "validation-only")
        if args.panel_manifest:
            if args.suite == "executions":
                raise ValueError("导出面板案例必须选择 panel 或 all 验收集合")
            validate_manifest_path(args.panel_manifest, new=True)
        target = TestDatabase(args.host, args.port, args.database, args.user, read_test_password(args.password_file))
        install_guard(target)
        with tempfile.TemporaryDirectory(prefix="dake-isolated-mysql-") as data_dir:
            os.environ.clear()
            os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                              RUN_MYSQL_TESTS="0", LLM_PROVIDER="stub", DATA_DIR=data_dir)
            sys.dont_write_bytecode = True
            prepare_database(target, data_dir)
            import pytest
            print(f"已核验隔离目标：{target.host}:{target.port}/{target.database}；仅使用虚构消息和假 Provider。")
            files = []
            if args.suite in {"executions", "all"}:
                files.append(str(root / "backend/tests/test_execution_mysql.py"))
            if args.suite in {"panel", "all"}:
                files.append(str(root / "backend/tests/test_execution_panel_mysql.py"))
            plugin = DatabasePlugin(target)
            # 使用本次独立临时目录，避免 pytest 清理其他测试遗留的环境文件而触发隔离保护。
            result = int(pytest.main(["-c", str(root / "backend/pyproject.toml"), "--noconftest", "-p", "no:cacheprovider", "-q", "--tb=short",
                                     "--basetemp", str(Path(data_dir) / "pytest"), *files], plugins=[plugin]))
            if result == 0 and args.panel_manifest:
                write_manifest(args.panel_manifest, target, plugin.panel_cases)
                print("面板实库用例已全部通过；虚构案例索引已保存，可另用只读账号启动合成服务。")
            return result
    except (ValueError, StorageError) as error:
        print(f"隔离验收未启动：{error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"隔离验收未完成（{type(error).__name__}）；未回落到其他数据库。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
