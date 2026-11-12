#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ports —— 宿主必须提供的能力契约。

为什么用 Protocol 而不是基类
---------------------------
``typing.Protocol`` 是结构化的：任何对象只要方法签名对得上就算实现，
不要求 import 本模块、不要求继承。这样
    * 测试可以塞一个 20 行的假对象；
    * Web 宿主可以塞一个「从上传的 zip 里读」的实现；
    * 桌面版塞真实文件系统实现；
谁都不需要为了「符合接口」去依赖别人的代码。

四个端口
--------
``LogSource``  发现并采集本机（或远端）的 Agent 日志 → 统一数据模型
``Store``      读写配置与产物（JSON / 文本 / 二进制）
``Clock``      取当前时间（可冻结，便于测试与复盘）
``Notifier``   把结论送出去（桌面通知 / 日志 / 聊天），Core 不关心渠道
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple


class LogSource(Protocol):
    """日志数据源。一个平台一个实现。"""

    name: str

    def discover(self, root: Optional[str] = None) -> List[Dict[str, Any]]:
        """发现有日志的工具根目录。

        返回项至少含：``tool`` / ``version`` / ``root`` / ``log_files`` /
        ``project_files``。返回空列表表示这台机器上没有可分析的数据。
        """
        ...

    def collect(self, tools: List[Dict[str, Any]], days: Optional[int] = None,
                log=None, root: Optional[str] = None,
                type_rules: Optional[Dict[str, Any]] = None
                ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """采集并归一化成 ``(data, ctx)``。

        ``data`` 至少有 ``meta`` / ``tasks`` / ``daily`` / ``models`` /
        ``automations``；``ctx`` 放采集过程的统计（文件数、跳过数、耗时）。
        """
        ...


class Store(Protocol):
    """配置与产物的读写。路径一律用「相对项目根」的形式，避免宿主路径泄漏进 Core。"""

    def read_json(self, rel: str, default: Any = None) -> Any:
        ...

    def write_json(self, rel: str, obj: Any) -> int:
        ...

    def write_text(self, rel: str, text: str) -> int:
        ...

    def exists(self, rel: str) -> bool:
        ...

    def resolve(self, rel: str) -> str:
        """相对路径 → 宿主可用的绝对路径（仅用于日志与审计展示）。"""
        ...


class Clock(Protocol):
    def now(self) -> str:
        """ISO 8601，精确到秒。"""
        ...

    def today(self) -> str:
        """YYYY-MM-DD。"""
        ...


class Notifier(Protocol):
    def emit(self, level: str, title: str, detail: str = "") -> None:
        """level ∈ ok / warn / crit / red / error。"""
        ...
