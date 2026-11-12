#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 子命令层 —— 每个文件是一条命令，薄封装，实现在 core/ 与 hosts/cli.py。

这一层属于 Host：它知道 argparse、print、退出码，
Core 不知道。按 `python -m hosts.commands.<name>` 调用。
"""
