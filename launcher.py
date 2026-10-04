# -*- coding: utf-8 -*-
"""
全球金融数据看板 · EXE 启动器
双击运行：启动本地数据服务 -> 自动打开浏览器。
命令行加 --no-browser 则不自动开浏览器（用于测试 / 静默运行）。
"""
import os
import socket
import sys
import threading
import time
import webbrowser

PORT = int(os.environ.get("GFD_PORT", "8770"))
URL = "http://127.0.0.1:%d/" % PORT


def port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def main():
    already = port_in_use(PORT)
    if already:
        print("[i] 端口 %d 已有看板在运行，直接打开浏览器。" % PORT)
    else:
        import server  # PyInstaller 打包后同包加载

        t = threading.Thread(target=server.main, daemon=True)
        t.start()
        # 等服务就绪（最多 10 秒）
        for _ in range(100):
            if port_in_use(PORT):
                break
            time.sleep(0.1)

    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)

    print("=" * 56)
    print("  全球金融数据看板已启动：%s" % URL)
    print("  关闭本窗口即退出服务。")
    print("=" * 56)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
