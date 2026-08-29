# -*- coding: utf-8 -*-
"""TT AI Screener 的统一入口。

用法：
  python main.py            # 网页 + MCP 远程服务（默认）
  python main.py --mcp      # 只跑 MCP stdio（给 Claude Desktop 用）

选项：
  --port PORT               端口，默认 8766

**这个项目是只读消费端。** 采集（往库里写行情、基本面、新闻）和建表全部在
tt-data-hub 那个仓库里，由服务器上的系统定时任务跑。所以这里没有
`--update`、没有 `--cron`、也不会建表——原来那几个开关连的都是已经搬走的代码。
"""
import argparse
import logging

logger = logging.getLogger("tt-trading")


def start_mcp_stdio():
    from server import mcp
    mcp.run()


def main():
    parser = argparse.ArgumentParser(description="TT AI Screener")
    parser.add_argument("--mcp", action="store_true",
                        help="只跑 MCP stdio（给 Claude Desktop 用）")
    parser.add_argument("--port", type=int, default=None,
                        help="端口，默认 8766")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.mcp:
        logger.info("Starting MCP server (stdio)")
        start_mcp_stdio()
    else:
        from server import run_server
        run_server(args.port)


if __name__ == "__main__":
    main()
