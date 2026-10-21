from __future__ import annotations

import os
import queue
import subprocess
import threading
import uuid
import webbrowser
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk

from pcl_companion import __version__
from pcl_companion.config import AppSettings, bundled_path
from pcl_companion.models import CatalogProject, InstallTask, MinecraftInstance, PclInstallation, ProjectVersion, VanillaVersion
from pcl_companion.services.install_engine import InstallEngine
from pcl_companion.services.mrpack_installer import MrpackError, MrpackInstaller
from pcl_companion.services.modrinth_service import CatalogError, ModrinthService
from pcl_companion.services.pcl_service import PclService
from pcl_companion.services.task_store import TaskStore
from pcl_companion.services.vanilla_service import VanillaError, VanillaService
from pcl_companion.ui.theme import COLORS, FONT


class CompanionApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"PCL Companion  {__version__}")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1180, screen_width - 80)
        window_height = min(780, screen_height - 100)
        offset_x = max((screen_width - window_width) // 2, 0)
        offset_y = max((screen_height - window_height) // 2, 0)
        self.root.geometry(f"{window_width}x{window_height}+{offset_x}+{offset_y}")
        self.root.minsize(min(1040, window_width), min(700, window_height))
        self.root.maxsize(max(window_width, screen_width - 40), max(window_height, screen_height - 60))
        self.root.configure(bg=COLORS["window"])
        assets_dir = bundled_path("assets")
        icon_path = assets_dir / "app_icon.ico"
        if icon_path.is_file():
            try:
                self.root.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        icon_png = assets_dir / "app_icon.png"
        if icon_png.is_file():
            try:
                icon_image = Image.open(icon_png).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
                self.window_icon = ImageTk.PhotoImage(icon_image)
                self.root.iconphoto(True, self.window_icon)
            except (OSError, tk.TclError):
                self.window_icon = None

        self.settings = AppSettings.load()
        self.pcl_service = PclService()
        self.modrinth = ModrinthService()
        self.vanilla = VanillaService()
        self.mrpack_installer = MrpackInstaller(self.vanilla)
        self.install_engine = InstallEngine()
        self.task_store = TaskStore()
        self.tasks = self.task_store.load()
        self._repair_saved_tasks()
        self.installation: PclInstallation | None = None
        self.instances: list[MinecraftInstance] = []
        self.catalog_projects: list[CatalogProject] = []
        self.catalog_type = "mod"
        self.catalog_type_names = {
            "vanilla": "原版游戏", "mod": "Mod", "modpack": "整合包", "datapack": "数据包",
            "resourcepack": "资源包", "shader": "光影包",
        }
        self.mod_category_names = {
            "全部分类": "", "性能优化": "optimization", "科技": "technology",
            "魔法": "magic", "冒险": "adventure", "装饰": "decoration",
            "工具": "utility", "世界生成": "worldgen", "生物": "mobs",
            "装备": "equipment", "社交": "social",
        }
        self.catalog_category_buttons: dict[str, tk.Button] = {}
        self.catalog_images: dict[str, ImageTk.PhotoImage] = {}
        self.vanilla_versions: list[VanillaVersion] = []
        self.catalog_generation = ""
        self.catalog_page = 0
        self.catalog_page_size = 8
        self.catalog_total = 0
        self.loading_animation_id: str | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.current_page = "home"

        self._configure_styles()
        self._build_shell()
        self._detect_environment()
        self.show_page("home")
        self.root.after(100, self._drain_events)
        threading.Thread(target=self._metadata_worker, daemon=True).start()

    def _repair_saved_tasks(self) -> None:
        changed = False
        download_dir = Path.home() / "Downloads" / "PCL Companion"
        packs = list(download_dir.glob("*.mrpack")) if download_dir.is_dir() else []
        for task in self.tasks:
            if task.state == "失败" and ("SSLError" in task.detail or "UNEXPECTED_EOF" in task.detail):
                task.detail = "连接 Modrinth CDN 时中断。已完成部分会使用校验缓存，可点击“重试部署”。"
                changed = True
            if not task.file_path and "Fabulously Optimized" in task.name:
                match = next((path for path in packs if "Fabulously.Optimized" in path.name or "Fabulously Optimized" in path.name), None)
                if match:
                    task.file_path = str(match)
                    changed = True
            elif not task.file_path and len(packs) == 1 and task.source == "Modrinth":
                task.file_path = str(packs[0])
                changed = True
        if changed:
            self.task_store.save(self.tasks)

    def run(self) -> None:
        self.root.mainloop()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "App.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=50,
            borderwidth=0,
            font=(FONT, 10),
        )
        style.map("App.Treeview", background=[("selected", "#244C87")])
        style.configure(
            "App.Treeview.Heading",
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            relief="flat",
            font=(FONT, 9, "bold"),
            padding=(8, 10),
        )
        style.map("App.Treeview.Heading", background=[("active", COLORS["surface_alt"])])
        style.configure(
            "App.TCombobox",
            fieldbackground=COLORS["surface_alt"],
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=7,
        )
        style.map("App.TCombobox", fieldbackground=[("readonly", COLORS["surface_alt"])], foreground=[("readonly", COLORS["text"])])
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=COLORS["surface_alt"],
            background=COLORS["primary"],
            borderwidth=0,
            lightcolor=COLORS["primary"],
            darkcolor=COLORS["primary"],
        )

    def _build_shell(self) -> None:
        sidebar = tk.Frame(self.root, bg=COLORS["sidebar"], width=218)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"], height=104)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        tk.Label(
            brand,
            text="◈",
            font=(FONT, 25, "bold"),
            fg=COLORS["primary"],
            bg=COLORS["sidebar"],
        ).pack(side="left", padx=(22, 10), pady=(25, 0))
        brand_text = tk.Frame(brand, bg=COLORS["sidebar"])
        brand_text.pack(side="left", pady=(25, 0))
        tk.Label(brand_text, text="PCL Companion", font=(FONT, 13, "bold"), fg=COLORS["text"], bg=COLORS["sidebar"]).pack(anchor="w")
        tk.Label(brand_text, text="下载与部署中心", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["sidebar"]).pack(anchor="w", pady=(2, 0))

        entries = (
            ("home", "⌂", "概览"),
            ("catalog", "⌕", "下载中心"),
            ("instances", "▦", "本地实例"),
            ("tasks", "⇣", "安装任务"),
            ("settings", "⚙", "设置"),
        )
        for key, icon, title in entries:
            button = tk.Button(
                sidebar,
                text=f"  {icon}    {title}",
                command=lambda name=key: self.show_page(name),
                anchor="w",
                relief="flat",
                bd=0,
                padx=18,
                pady=12,
                font=(FONT, 10),
                fg=COLORS["muted"],
                bg=COLORS["sidebar"],
                activebackground=COLORS["surface_alt"],
                activeforeground=COLORS["text"],
                cursor="hand2",
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button

        self.sidebar_status = tk.Label(
            sidebar,
            text="●  正在检测 PCL…",
            font=(FONT, 9),
            fg=COLORS["warning"],
            bg=COLORS["sidebar"],
            anchor="w",
            padx=18,
        )
        self.sidebar_status.pack(side="bottom", fill="x", pady=(0, 22))

        self.content = tk.Frame(self.root, bg=COLORS["window"])
        self.content.pack(side="left", fill="both", expand=True)
        self._build_home()
        self._build_catalog()
        self._build_instances()
        self._build_tasks()
        self._build_settings()

    def _page(self, key: str, title: str, subtitle: str) -> tuple[tk.Frame, tk.Frame]:
        page = tk.Frame(self.content, bg=COLORS["window"])
        self.pages[key] = page
        heading = tk.Frame(page, bg=COLORS["window"], height=72)
        heading.pack(fill="x", padx=34, pady=(18, 0))
        heading.pack_propagate(False)
        tk.Label(heading, text=title, font=(FONT, 22, "bold"), fg=COLORS["text"], bg=COLORS["window"]).pack(anchor="w")
        tk.Label(heading, text=subtitle, font=(FONT, 10), fg=COLORS["muted"], bg=COLORS["window"]).pack(anchor="w", pady=(6, 0))
        body = tk.Frame(page, bg=COLORS["window"])
        body.pack(fill="both", expand=True, padx=34, pady=(0, 18))
        return page, body

    def _card(self, parent: tk.Widget, padding: int = 20) -> tk.Frame:
        outer = tk.Frame(parent, bg=COLORS["border"])
        inner = tk.Frame(outer, bg=COLORS["surface"], padx=padding, pady=padding)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    def _button(self, parent: tk.Widget, text: str, command, primary: bool = True, width: int | None = None) -> tk.Button:
        bg = COLORS["primary"] if primary else COLORS["surface_alt"]
        active = COLORS["primary_hover"] if primary else COLORS["border"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            relief="flat",
            bd=0,
            padx=16,
            pady=9,
            cursor="hand2",
            font=(FONT, 9, "bold"),
            fg="white" if primary else COLORS["text"],
            bg=bg,
            activebackground=active,
            activeforeground="white" if primary else COLORS["text"],
        )
        self._animate_button_hover(button, bg, active)
        return button

    def _animate_button_hover(self, button: tk.Button, normal: str, hover: str) -> None:
        button.bind("<Enter>", lambda _e: self._tween_color(button, normal, hover), add="+")
        button.bind("<Leave>", lambda _e: self._tween_color(button, hover, normal), add="+")

    def _tween_color(self, widget: tk.Widget, start: str, end: str, step: int = 0) -> None:
        if not widget.winfo_exists():
            return
        start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
        end_rgb = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
        ratio = min(step / 6, 1)
        color = "#" + "".join(f"{round(a + (b - a) * ratio):02X}" for a, b in zip(start_rgb, end_rgb))
        widget.configure(bg=color)
        if step < 6:
            widget.after(16, lambda: self._tween_color(widget, start, end, step + 1))

    def _build_home(self) -> None:
        _, body = self._page("home", "概览", "检查 PCL 状态、部署目标和本地实例")
        hero_outer = tk.Frame(body, bg=COLORS["border"])
        hero_outer.pack(fill="x")
        hero = tk.Frame(hero_outer, bg="#142B50", padx=26, pady=24)
        hero.pack(fill="x", padx=1, pady=1)
        left = tk.Frame(hero, bg="#142B50")
        left.pack(side="left", fill="both", expand=True)
        self.home_status_title = tk.Label(left, text="正在检测本地环境", font=(FONT, 17, "bold"), fg="white", bg="#142B50")
        self.home_status_title.pack(anchor="w")
        self.home_status_detail = tk.Label(left, text="正在查找 PCL 与 .minecraft…", font=(FONT, 10), fg="#B7CCEA", bg="#142B50")
        self.home_status_detail.pack(anchor="w", pady=(8, 0))
        actions = tk.Frame(hero, bg="#142B50")
        actions.pack(side="right")
        self.launch_button = self._button(actions, "打开 PCL", self._launch_pcl)
        self.launch_button.pack(side="left", padx=5)
        self._button(actions, "重新检测", self._detect_environment, primary=False).pack(side="left", padx=5)

        stats = tk.Frame(body, bg=COLORS["window"])
        stats.pack(fill="x", pady=18)
        self.stat_instances = self._stat_card(stats, "0", "本地实例", COLORS["primary"])
        self.stat_modded = self._stat_card(stats, "0", "可安装 Mod", COLORS["success"])
        self.stat_tasks = self._stat_card(stats, str(len(self.tasks)), "安装任务", COLORS["warning"])

        lower = tk.Frame(body, bg=COLORS["window"])
        lower.pack(fill="both", expand=True)
        target_outer = tk.Frame(lower, bg=COLORS["border"])
        target_outer.pack(side="left", fill="both", expand=True, padx=(0, 9))
        target = tk.Frame(target_outer, bg=COLORS["surface"], padx=22, pady=20)
        target.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(target, text="当前部署目标", font=(FONT, 12, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).pack(anchor="w")
        self.target_path_label = tk.Label(target, text="尚未检测到", font=("Cascadia Mono", 10), fg=COLORS["primary_hover"], bg=COLORS["surface"], wraplength=530, justify="left")
        self.target_path_label.pack(anchor="w", pady=(16, 8))
        self.target_hint_label = tk.Label(target, text="完成安装后，请在 PCL 中右键当前文件夹 → 刷新。", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"], wraplength=520, justify="left")
        self.target_hint_label.pack(anchor="w")

        guide_outer = tk.Frame(lower, bg=COLORS["border"], width=310)
        guide_outer.pack(side="left", fill="both", padx=(9, 0))
        guide_outer.pack_propagate(False)
        guide = tk.Frame(guide_outer, bg=COLORS["surface"], padx=22, pady=20)
        guide.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(guide, text="部署流程", font=(FONT, 12, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).pack(anchor="w")
        for index, text in enumerate(("选择整合包", "解析环境与依赖", "临时目录构建", "部署后手动刷新"), start=1):
            row = tk.Frame(guide, bg=COLORS["surface"])
            row.pack(fill="x", pady=(14 if index == 1 else 9, 0))
            tk.Label(row, text=str(index), width=2, font=(FONT, 9, "bold"), fg="white", bg=COLORS["primary"]).pack(side="left")
            tk.Label(row, text=text, font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"]).pack(side="left", padx=10)

    def _stat_card(self, parent: tk.Widget, value: str, label: str, color: str) -> tk.Label:
        outer = tk.Frame(parent, bg=COLORS["border"])
        outer.pack(side="left", fill="x", expand=True, padx=6)
        inner = tk.Frame(outer, bg=COLORS["surface"], padx=20, pady=16)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        value_label = tk.Label(inner, text=value, font=(FONT, 21, "bold"), fg=color, bg=COLORS["surface"])
        value_label.pack(anchor="w")
        tk.Label(inner, text=label, font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"]).pack(anchor="w", pady=(2, 0))
        return value_label

    def _build_catalog(self) -> None:
        _, body = self._page("catalog", "下载中心", "浏览并下载 Modrinth 资源；文件将经过哈希校验")
        category = tk.Frame(body, bg=COLORS["surface"], width=150)
        category.pack(side="left", fill="y", padx=(0, 14))
        category.pack_propagate(False)
        tk.Label(category, text="社区资源", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"]).pack(anchor="w", padx=16, pady=(18, 8))
        category_items = (
            ("vanilla", "▣", "原版游戏"), ("mod", "♧", "Mod"), ("modpack", "◇", "整合包"),
            ("datapack", "⌘", "数据包"), ("resourcepack", "▧", "资源包"),
            ("shader", "☼", "光影包"),
        )
        for key, icon, title in category_items:
            button = tk.Button(
                category, text=f" {icon}   {title}", anchor="w", relief="flat", bd=0,
                padx=14, pady=12, font=(FONT, 10), cursor="hand2",
                command=lambda value=key: self._select_catalog_type(value),
                fg=COLORS["muted"], bg=COLORS["surface"],
                activeforeground=COLORS["text"], activebackground=COLORS["surface_alt"],
            )
            button.pack(fill="x", pady=1)
            self.catalog_category_buttons[key] = button

        panel = tk.Frame(body, bg=COLORS["window"])
        panel.pack(side="left", fill="both", expand=True)
        filters_outer = tk.Frame(panel, bg=COLORS["border"])
        filters_outer.pack(fill="x", pady=(0, 14))
        filters = tk.Frame(filters_outer, bg=COLORS["surface"], padx=14, pady=12)
        filters.pack(fill="x", padx=1, pady=1)
        self.catalog_heading = tk.Label(filters, text="搜索 Mod", font=(FONT, 12, "bold"), fg=COLORS["text"], bg=COLORS["surface"])
        self.catalog_heading.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        self.search_var = tk.StringVar()
        search = tk.Entry(filters, textvariable=self.search_var, font=(FONT, 10), fg=COLORS["text"], bg=COLORS["surface_alt"], insertbackground=COLORS["text"], relief="flat", bd=0)
        search.grid(row=1, column=0, columnspan=5, sticky="ew", ipady=8)
        search.insert(0, "输入名称或关键词…")
        search.bind("<FocusIn>", lambda _e: self._clear_placeholder(search))
        search.bind("<Return>", lambda _e: self._search_catalog())
        self.game_version_var = tk.StringVar(value="全部版本")
        self.game_version_combo = ttk.Combobox(filters, textvariable=self.game_version_var, values=("全部版本", "1.21.1", "1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"), state="readonly", width=12, style="App.TCombobox")
        self.game_version_combo.grid(row=2, column=0, padx=(0, 5), pady=(10, 0))
        self.loader_var = tk.StringVar(value="全部加载器")
        loader = ttk.Combobox(filters, textvariable=self.loader_var, values=("全部加载器", "Fabric", "Forge", "NeoForge", "Quilt"), state="readonly", width=13, style="App.TCombobox")
        loader.grid(row=2, column=1, padx=5, pady=(10, 0))
        self.mod_category_var = tk.StringVar(value="全部分类")
        self.mod_category_combo = ttk.Combobox(
            filters, textvariable=self.mod_category_var,
            values=tuple(self.mod_category_names), state="readonly", width=11,
            style="App.TCombobox",
        )
        self.mod_category_combo.grid(row=2, column=2, padx=5, pady=(10, 0))
        actions = tk.Frame(filters, bg=COLORS["surface"])
        actions.grid(row=2, column=3, columnspan=2, sticky="e", pady=(10, 0))
        self._button(actions, "搜索", self._search_catalog, width=7).pack(side="left")
        self._button(actions, "重置", self._reset_catalog_filters, primary=False, width=7).pack(side="left", padx=(6, 0))
        filters.grid_columnconfigure(0, weight=1)
        filters.grid_columnconfigure(1, weight=1)

        table_outer = tk.Frame(panel, bg=COLORS["border"])
        table_outer.pack(fill="both", expand=True)
        table_frame = tk.Frame(table_outer, bg=COLORS["surface"])
        table_frame.pack(fill="both", expand=True, padx=1, pady=1)
        columns = ("description", "loader")
        self.catalog_tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", height=4, style="App.Treeview")
        self.catalog_tree.heading("#0", text="名称")
        self.catalog_tree.heading("description", text="简介")
        self.catalog_tree.heading("loader", text="加载器")
        self.catalog_tree.column("#0", width=190, anchor="w")
        self.catalog_tree.column("description", width=330, anchor="w")
        self.catalog_tree.column("loader", width=100, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.catalog_tree.yview)
        self.catalog_tree.configure(yscrollcommand=scroll.set)
        self.catalog_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.catalog_tree.bind("<Double-1>", lambda _e: self._catalog_primary_action())
        self.catalog_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_catalog_detail())

        detail = tk.Frame(panel, bg=COLORS["surface"], padx=14, pady=8, height=108)
        detail.pack(fill="x", pady=(10, 0))
        detail.pack_propagate(False)
        self.catalog_detail_title = tk.Label(detail, text="选择一个资源查看完整信息", font=(FONT, 10, "bold"), fg=COLORS["text"], bg=COLORS["surface"], anchor="w")
        self.catalog_detail_title.pack(fill="x")
        self.catalog_detail_text = tk.Label(detail, text="", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"], anchor="nw", justify="left", wraplength=560)
        self.catalog_detail_text.pack(fill="both", expand=True, pady=(5, 0))

        footer = tk.Frame(panel, bg=COLORS["window"])
        footer.pack(fill="x", pady=(12, 0))
        self.catalog_status = tk.Label(footer, text="点击搜索加载 Modrinth 资源", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["window"])
        self.catalog_status.pack(side="left")
        pager = tk.Frame(footer, bg=COLORS["window"])
        pager.pack(side="left", padx=18)
        self.catalog_prev_button = self._button(pager, "上一页", self._previous_catalog_page, primary=False, width=7)
        self.catalog_prev_button.pack(side="left")
        self.catalog_page_label = tk.Label(pager, text="第 1 页", font=(FONT, 9), fg=COLORS["text"], bg=COLORS["window"], width=12)
        self.catalog_page_label.pack(side="left", padx=4)
        self.catalog_next_button = self._button(pager, "下一页", self._next_catalog_page, primary=False, width=7)
        self.catalog_next_button.pack(side="left")
        self._button(footer, "查看详情", self._open_selected_project, primary=False).pack(side="right", padx=5)
        self.catalog_primary_button = self._button(footer, "选择版本并下载", self._catalog_primary_action)
        self.catalog_primary_button.pack(side="right", padx=5)
        # Reserve the detail and paging controls before allowing the table to expand.
        table_outer.pack_forget()
        detail.pack_forget()
        footer.pack_forget()
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        detail.pack(side="bottom", fill="x", pady=(10, 0))
        table_outer.pack(fill="both", expand=True)
        self._select_catalog_type("mod", search=False)

    def _build_instances(self) -> None:
        _, body = self._page("instances", "本地实例", "读取 PCL 当前 .minecraft 中已经安装的版本")
        table_outer = tk.Frame(body, bg=COLORS["border"])
        table_outer.pack(fill="both", expand=True)
        table_frame = tk.Frame(table_outer, bg=COLORS["surface"])
        table_frame.pack(fill="both", expand=True, padx=1, pady=1)
        columns = ("name", "minecraft", "loader", "java", "state")
        self.instance_tree = ttk.Treeview(table_frame, columns=columns, show="headings", style="App.Treeview")
        for key, text, width in (("name", "实例名称", 390), ("minecraft", "Minecraft", 130), ("loader", "加载器", 180), ("java", "Java", 90), ("state", "状态", 110)):
            self.instance_tree.heading(key, text=text)
            self.instance_tree.column(key, width=width, anchor="w" if key == "name" else "center")
        self.instance_tree.pack(fill="both", expand=True)
        footer = tk.Frame(body, bg=COLORS["window"])
        footer.pack(fill="x", pady=(12, 0))
        self.instance_status = tk.Label(footer, text="尚未扫描", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["window"])
        self.instance_status.pack(side="left")
        self._button(footer, "打开实例文件夹", self._open_selected_instance, primary=False).pack(side="right", padx=5)
        self._button(footer, "重新扫描", self._refresh_instances).pack(side="right", padx=5)

    def _build_tasks(self) -> None:
        _, body = self._page("tasks", "安装任务", "安装将先在临时目录完成，验证后再提交到 PCL")
        self.task_container = tk.Frame(body, bg=COLORS["window"])
        self.task_container.pack(fill="both", expand=True)
        footer = tk.Frame(body, bg=COLORS["window"])
        footer.pack(fill="x", pady=(12, 0))
        self._button(footer, "清除已取消任务", self._clear_tasks, primary=False).pack(side="right")
        self._render_tasks()

    def _build_settings(self) -> None:
        _, body = self._page("settings", "设置", "配置 PCL 程序和 Minecraft 部署目标")
        card = self._card(body, 24)
        card.pack(fill="x")
        tk.Label(card, text="PCL 程序", font=(FONT, 11, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).grid(row=0, column=0, sticky="w")
        self.pcl_path_var = tk.StringVar(value=self.settings.pcl_executable)
        self.minecraft_path_var = tk.StringVar(value=self.settings.minecraft_dir)
        self.deployment_mode_var = tk.StringVar(value=self.settings.deployment_mode)
        self.custom_minecraft_path_var = tk.StringVar(value=self.settings.custom_minecraft_dir)
        self._path_row(card, 1, self.pcl_path_var, self._choose_pcl)
        tk.Label(card, text="部署目标", font=(FONT, 11, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).grid(row=2, column=0, sticky="w", pady=(20, 0))
        modes = tk.Frame(card, bg=COLORS["surface"])
        modes.grid(row=3, column=0, sticky="w", pady=(8, 0))
        for text, value in (("PCL 当前 .minecraft", "pcl"), ("自定义目录", "custom")):
            tk.Radiobutton(
                modes, text=text, value=value, variable=self.deployment_mode_var,
                command=self._update_deployment_controls,
                font=(FONT, 9), fg=COLORS["text"], bg=COLORS["surface"],
                activebackground=COLORS["surface"], selectcolor=COLORS["surface_alt"],
            ).pack(side="left", padx=(0, 20))
        tk.Label(card, text="自定义 .minecraft", font=(FONT, 9, "bold"), fg=COLORS["muted"], bg=COLORS["surface"]).grid(row=4, column=0, sticky="w", pady=(14, 0))
        self.custom_path_line = self._path_row(card, 5, self.custom_minecraft_path_var, self._choose_custom_minecraft)
        tk.Label(card, text="当前生效目录", font=(FONT, 9, "bold"), fg=COLORS["muted"], bg=COLORS["surface"]).grid(row=6, column=0, sticky="w", pady=(14, 0))
        current = tk.Frame(card, bg=COLORS["surface_alt"])
        current.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        tk.Label(current, textvariable=self.minecraft_path_var, font=("Cascadia Mono", 9), fg=COLORS["primary"], bg=COLORS["surface_alt"], anchor="w", padx=12, pady=10).pack(fill="x")
        card.grid_columnconfigure(0, weight=1)
        self._update_deployment_controls()
        notice = tk.Frame(body, bg="#3B2D18", padx=20, pady=16)
        notice.pack(fill="x", pady=18)
        tk.Label(notice, text="方案一：安装后手动刷新", font=(FONT, 10, "bold"), fg=COLORS["warning"], bg="#3B2D18").pack(anchor="w")
        tk.Label(notice, text="Companion 不修改 PCL 缓存。安装成功后，请在 PCL 版本选择页右键“当前文件夹”并点击“刷新”。", font=(FONT, 9), fg="#E9D6B5", bg="#3B2D18", wraplength=840, justify="left").pack(anchor="w", pady=(6, 0))
        actions = tk.Frame(body, bg=COLORS["window"])
        actions.pack(fill="x")
        self._button(actions, "保存设置", self._save_settings).pack(side="right")

    def _path_row(self, parent: tk.Widget, row: int, variable: tk.StringVar, command) -> tk.Frame:
        line = tk.Frame(parent, bg=COLORS["surface_alt"])
        line.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        tk.Label(line, textvariable=variable, font=("Cascadia Mono", 9), fg=COLORS["text"], bg=COLORS["surface_alt"], anchor="w", padx=12).pack(side="left", fill="x", expand=True)
        self._button(line, "选择", command, primary=False, width=7).pack(side="right")
        return line

    def show_page(self, key: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.current_page = key
        for name, button in self.nav_buttons.items():
            selected = name == key
            button.configure(bg=COLORS["surface_alt"] if selected else COLORS["sidebar"], fg=COLORS["text"] if selected else COLORS["muted"])
        if key == "instances":
            self._render_instances()
        elif key == "tasks":
            self._render_tasks()

    def _detect_environment(self) -> None:
        self.home_status_title.configure(text="正在检测本地环境")
        self.home_status_detail.configure(text="正在查找 PCL 与 .minecraft…")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self) -> None:
        configured_target = self.settings.custom_minecraft_dir if self.settings.deployment_mode == "custom" else ""
        installation = self.pcl_service.detect(
            self.settings.pcl_executable,
            configured_target,
        )
        instances = self.pcl_service.list_instances(installation.minecraft_dir) if installation else []
        self.events.put(("environment", (installation, instances)))

    def _refresh_instances(self) -> None:
        if not self.installation:
            messagebox.showwarning("未找到 PCL", "请先在设置中选择 PCL 程序和 Minecraft 文件夹。")
            return
        self.instance_status.configure(text="正在重新扫描…")
        threading.Thread(target=lambda: self.events.put(("instances", self.pcl_service.list_instances(self.installation.minecraft_dir))), daemon=True).start()

    def _apply_environment(self, installation: PclInstallation | None, instances: list[MinecraftInstance]) -> None:
        self.installation = installation
        self.instances = instances
        if installation:
            self.settings.pcl_executable = str(installation.executable)
            self.settings.minecraft_dir = str(installation.minecraft_dir)
            self.settings.save()
            self.pcl_path_var.set(str(installation.executable))
            self.minecraft_path_var.set(str(installation.minecraft_dir))
            state = "运行中" if installation.is_running else "已检测"
            self.sidebar_status.configure(text=f"●  PCL {state}", fg=COLORS["success"])
            self.home_status_title.configure(text=f"PCL {installation.version} · {state}")
            self.home_status_detail.configure(text=str(installation.executable))
            self.target_path_label.configure(text=str(installation.minecraft_dir / "versions"))
        else:
            self.sidebar_status.configure(text="●  未找到 PCL", fg=COLORS["danger"])
            self.home_status_title.configure(text="未找到 PCL")
            self.home_status_detail.configure(text="请在设置中选择 Plain Craft Launcher 2.exe")
            self.target_path_label.configure(text="尚未配置")
        self._update_stats()
        self._render_instances()

    def _update_stats(self) -> None:
        self.stat_instances.configure(text=str(len(self.instances)))
        self.stat_modded.configure(text=str(sum(item.loader != "Vanilla" for item in self.instances if item.valid)))
        self.stat_tasks.configure(text=str(len(self.tasks)))

    def _render_instances(self) -> None:
        if not hasattr(self, "instance_tree"):
            return
        self.instance_tree.delete(*self.instance_tree.get_children())
        for index, item in enumerate(self.instances):
            loader = item.loader + (f" {item.loader_version}" if item.loader_version else "")
            self.instance_tree.insert("", "end", iid=str(index), values=(item.name, item.minecraft_version, loader, item.java_major or "自动", "可用" if item.valid else "错误"))
        self.instance_status.configure(text=f"已找到 {len(self.instances)} 个实例")

    def _search_catalog(self, reset_page: bool = True) -> None:
        if reset_page:
            self.catalog_page = 0
        query = self.search_var.get().strip()
        if query == "输入名称或关键词…":
            query = ""
        game_version = "" if self.game_version_var.get() == "全部版本" else self.game_version_var.get()
        loader = "" if self.loader_var.get() == "全部加载器" else self.loader_var.get()
        category = self.mod_category_names.get(self.mod_category_var.get(), "") if self.catalog_type == "mod" else ""
        self.catalog_status.configure(text="正在连接 Modrinth…", fg=COLORS["warning"])
        source_name = "Mojang" if self.catalog_type == "vanilla" else "Modrinth"
        self._start_loading_animation(f"正在连接 {source_name}")
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        self.catalog_generation = uuid.uuid4().hex
        if self.catalog_type == "vanilla":
            threading.Thread(target=self._vanilla_catalog_worker, args=(self.catalog_generation, query), daemon=True).start()
        else:
            threading.Thread(target=self._catalog_worker, args=(self.catalog_generation, query, self.catalog_type, game_version, loader, category, self.catalog_page), daemon=True).start()

    def _vanilla_catalog_worker(self, generation: str, query: str) -> None:
        try:
            versions = self.vanilla.list_versions(True)
            if query:
                versions = [item for item in versions if query.casefold() in item.version_id.casefold()]
            self.events.put(("vanilla_catalog", (generation, versions)))
        except VanillaError as exc:
            self.events.put(("catalog_error", str(exc)))

    def _catalog_worker(self, generation: str, query: str, project_type: str, game_version: str, loader: str, category: str, page: int) -> None:
        try:
            result, total = self.modrinth.search_projects_page(
                query, project_type, game_version, loader, category,
                self.catalog_page_size, page * self.catalog_page_size,
            )
            self.events.put(("catalog", (generation, result, total)))
        except CatalogError as exc:
            self.events.put(("catalog_error", str(exc)))

    def _render_catalog(self, generation: str, projects: list[CatalogProject], total: int) -> None:
        if generation != self.catalog_generation:
            return
        self._stop_loading_animation()
        self.catalog_projects = projects
        self.catalog_total = total
        self.catalog_images.clear()
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        for index, project in enumerate(projects):
            loaders = " / ".join(name.title() for name in project.loaders) or "按版本确定"
            summary = project.description.replace("\n", " ")
            if len(summary) > 72:
                summary = summary[:69] + "…"
            self.catalog_tree.insert(
                "", "end", iid=str(index), text=f"  {project.title}",
                values=(summary, loaders),
            )
            if project.icon_url:
                threading.Thread(target=self._icon_worker, args=(generation, str(index), project.icon_url), daemon=True).start()
        if projects:
            self.catalog_tree.selection_set("0")
            self._update_catalog_detail()
        resource_name = self.catalog_type_names[self.catalog_type]
        total_pages = max(1, (total + self.catalog_page_size - 1) // self.catalog_page_size)
        self.catalog_page_label.configure(text=f"第 {self.catalog_page + 1} / {total_pages} 页")
        self.catalog_prev_button.configure(state="normal" if self.catalog_page > 0 else "disabled")
        self.catalog_next_button.configure(state="normal" if self.catalog_page + 1 < total_pages else "disabled")
        self.catalog_status.configure(text=f"本页 {len(projects)} 条 · 共 {total} 条 {resource_name}", fg=COLORS["muted"])

    def _render_vanilla_catalog(self, generation: str, versions: list[VanillaVersion]) -> None:
        if generation != self.catalog_generation:
            return
        self._stop_loading_animation()
        self.vanilla_versions = versions
        self.catalog_projects = []
        self.catalog_images.clear()
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        kind_names = {"release": "正式版", "snapshot": "快照版", "old_beta": "远古测试版", "old_alpha": "远古版"}
        for index, version in enumerate(versions):
            kind = kind_names.get(version.version_type, version.version_type)
            self.catalog_tree.insert("", "end", iid=str(index), text=f"  Minecraft {version.version_id}", values=(
                f"Mojang 官方原版 · {kind} · {version.release_time[:10]}", "原版",
            ))
        self.catalog_status.configure(text=f"已加载 {len(versions)} 个官方原版版本", fg=COLORS["muted"])
        self.catalog_page_label.configure(text="官方清单")
        self.catalog_prev_button.configure(state="disabled")
        self.catalog_next_button.configure(state="disabled")

    def _select_catalog_type(self, project_type: str, search: bool = True) -> None:
        self.catalog_type = project_type
        title = self.catalog_type_names[project_type]
        self.catalog_heading.configure(text=f"搜索 {title}")
        for key, button in self.catalog_category_buttons.items():
            selected = key == project_type
            button.configure(
                bg=COLORS["surface_alt"] if selected else COLORS["surface"],
                fg=COLORS["primary"] if selected else COLORS["muted"],
            )
        self.mod_category_combo.configure(state="readonly" if project_type == "mod" else "disabled")
        filter_state = "disabled" if project_type == "vanilla" else "readonly"
        self.loader_var.set("全部加载器")
        self.game_version_var.set("全部版本")
        self.game_version_combo.configure(state=filter_state)
        # Loader is the other bound combobox in the same filter row.
        for widget in self.game_version_combo.master.winfo_children():
            if isinstance(widget, ttk.Combobox) and widget is not self.game_version_combo and widget is not self.mod_category_combo:
                widget.configure(state=filter_state)
        self.catalog_primary_button.configure(text="安装所选原版" if project_type == "vanilla" else "选择版本并下载")
        if search:
            self._search_catalog()

    def _previous_catalog_page(self) -> None:
        if self.catalog_type != "vanilla" and self.catalog_page > 0:
            self.catalog_page -= 1
            self._search_catalog(reset_page=False)

    def _next_catalog_page(self) -> None:
        if self.catalog_type == "vanilla":
            return
        total_pages = max(1, (self.catalog_total + self.catalog_page_size - 1) // self.catalog_page_size)
        if self.catalog_page + 1 < total_pages:
            self.catalog_page += 1
            self._search_catalog(reset_page=False)

    def _catalog_primary_action(self) -> None:
        if self.catalog_type == "vanilla":
            self._install_selected_vanilla()
        else:
            self._load_selected_versions()

    def _update_catalog_detail(self) -> None:
        selected = self.catalog_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if self.catalog_type == "vanilla":
            if index >= len(self.vanilla_versions):
                return
            item = self.vanilla_versions[index]
            self.catalog_detail_title.configure(text=f"Minecraft {item.version_id}")
            self.catalog_detail_text.configure(text=f"类型：{item.version_type}    发布时间：{item.release_time[:19]}    来源：Mojang 官方版本清单")
        elif index < len(self.catalog_projects):
            item = self.catalog_projects[index]
            versions = item.latest_version or "按发布版本确定"
            categories = " / ".join(item.categories[:8]) or "无"
            self.catalog_detail_title.configure(text=item.title)
            self.catalog_detail_text.configure(text=f"{item.description}\n支持版本：{versions}    分类：{categories}    下载量：{item.downloads:,}")

    def _reset_catalog_filters(self) -> None:
        self.search_var.set("")
        self.game_version_var.set("全部版本")
        self.loader_var.set("全部加载器")
        self.mod_category_var.set("全部分类")
        self._search_catalog()

    def _metadata_worker(self) -> None:
        try:
            self.events.put(("game_versions", self.modrinth.get_game_versions()))
        except CatalogError:
            pass

    def _icon_worker(self, generation: str, item_id: str, url: str) -> None:
        data = self.modrinth.get_icon(url)
        if data:
            self.events.put(("catalog_icon", (generation, item_id, data)))

    def _apply_catalog_icon(self, item_id: str, data: bytes) -> None:
        if not self.catalog_tree.exists(item_id):
            return
        try:
            image = Image.open(BytesIO(data)).convert("RGBA")
            image.thumbnail((42, 42), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (46, 46), (255, 255, 255, 0))
            canvas.alpha_composite(image, ((46 - image.width) // 2, (46 - image.height) // 2))
            photo = ImageTk.PhotoImage(canvas)
            self.catalog_images[item_id] = photo
            self.catalog_tree.item(item_id, image=photo)
        except (OSError, ValueError):
            return

    def _start_loading_animation(self, label: str) -> None:
        self._stop_loading_animation()
        def tick(step: int = 0) -> None:
            self.catalog_status.configure(text=label + "." * (step % 4))
            self.loading_animation_id = self.root.after(260, lambda: tick(step + 1))
        tick()

    def _stop_loading_animation(self) -> None:
        if self.loading_animation_id:
            self.root.after_cancel(self.loading_animation_id)
            self.loading_animation_id = None

    def _selected_project(self) -> CatalogProject | None:
        selected = self.catalog_tree.selection()
        if not selected:
            return None
        try:
            return self.catalog_projects[int(selected[0])]
        except (IndexError, ValueError):
            return None

    def _open_selected_project(self) -> None:
        if self.catalog_type == "vanilla":
            selected = self.catalog_tree.selection()
            if selected and int(selected[0]) < len(self.vanilla_versions):
                version = self.vanilla_versions[int(selected[0])]
                webbrowser.open(f"https://zh.minecraft.wiki/w/Java版{version.version_id}")
            return
        project = self._selected_project()
        if not project:
            messagebox.showinfo("选择整合包", "请先选择一个整合包。")
            return
        webbrowser.open(f"https://modrinth.com/{self.catalog_type}/{project.slug}")

    def _load_selected_versions(self) -> None:
        project = self._selected_project()
        if not project:
            messagebox.showinfo("选择资源", "请先选择一个资源。")
            return
        game_version = "" if self.game_version_var.get() == "全部版本" else self.game_version_var.get()
        loader = "" if self.loader_var.get() == "全部加载器" else self.loader_var.get()
        self.catalog_status.configure(text=f"正在读取 {project.title} 的版本…", fg=COLORS["warning"])
        threading.Thread(
            target=self._versions_worker,
            args=(project, game_version, loader),
            daemon=True,
        ).start()

    def _versions_worker(self, project: CatalogProject, game_version: str, loader: str) -> None:
        try:
            versions = self.modrinth.get_versions(project.project_id, game_version, loader)
            self.events.put(("versions", (project, versions)))
        except CatalogError as exc:
            self.events.put(("catalog_error", str(exc)))

    def _show_version_dialog(self, project: CatalogProject, versions: list[ProjectVersion]) -> None:
        self.catalog_status.configure(text=f"{project.title}：找到 {len(versions)} 个可下载版本", fg=COLORS["muted"])
        if not versions:
            messagebox.showinfo("没有匹配版本", "当前游戏版本或加载器筛选下没有可下载文件。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f"选择版本 · {project.title}")
        dialog.geometry("820x520")
        dialog.minsize(700, 420)
        dialog.configure(bg=COLORS["window"])
        dialog.transient(self.root)
        dialog.grab_set()
        heading = tk.Frame(dialog, bg=COLORS["surface"], padx=22, pady=18)
        heading.pack(fill="x")
        tk.Label(heading, text=project.title, font=(FONT, 15, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).pack(anchor="w")
        tk.Label(heading, text="选择一个发布版本并下载原始文件", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"]).pack(anchor="w", pady=(5, 0))
        columns = ("version", "minecraft", "loader", "published", "size")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", style="App.Treeview")
        for key, text, width in (("version", "版本", 230), ("minecraft", "Minecraft", 150), ("loader", "加载器", 120), ("published", "发布时间", 120), ("size", "大小", 90)):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w" if key == "version" else "center")
        for index, version in enumerate(versions):
            file = version.primary_file
            size = self._format_size(file.size) if file else "无文件"
            tree.insert("", "end", iid=str(index), values=(
                version.version_number or version.name,
                " / ".join(version.game_versions[:3]),
                " / ".join(version.loaders),
                version.published[:10], size,
            ))
        tree.pack(fill="both", expand=True, padx=22, pady=18)
        if versions:
            tree.selection_set("0")
        footer = tk.Frame(dialog, bg=COLORS["window"])
        footer.pack(fill="x", padx=22, pady=(0, 18))
        self._button(footer, "取消", dialog.destroy, primary=False).pack(side="right")
        self._button(
            footer, "下载所选版本",
            lambda: self._download_selected_version(dialog, tree, project, versions),
        ).pack(side="right", padx=10)
        tree.bind("<Double-1>", lambda _e: self._download_selected_version(dialog, tree, project, versions))

    def _download_selected_version(self, dialog: tk.Toplevel, tree: ttk.Treeview, project: CatalogProject, versions: list[ProjectVersion]) -> None:
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("选择版本", "请先选择一个版本。", parent=dialog)
            return
        version = versions[int(selected[0])]
        file = version.primary_file
        if not file:
            messagebox.showerror("无法下载", "该版本没有可下载文件。", parent=dialog)
            return
        default_dir = Path.home() / "Downloads" / "PCL Companion"
        default_dir.mkdir(parents=True, exist_ok=True)
        destination = filedialog.asksaveasfilename(
            parent=dialog,
            title="保存 Modrinth 文件",
            initialdir=str(default_dir),
            initialfile=file.filename,
            defaultextension=Path(file.filename).suffix,
        )
        if not destination:
            return
        dialog.destroy()
        task = InstallTask(
            task_id=uuid.uuid4().hex[:12], name=f"{project.title} · {version.version_number}",
            source="Modrinth", state="下载中", detail=str(destination),
            project_type=self.catalog_type, file_path=str(destination),
        )
        self.tasks.append(task)
        self.task_store.save(self.tasks)
        self._update_stats()
        self.show_page("tasks")
        threading.Thread(target=self._download_worker, args=(task, file, Path(destination)), daemon=True).start()

    def _download_worker(self, task: InstallTask, file, target: Path) -> None:
        try:
            last_progress = -1.0
            def report(received: int, total: int) -> None:
                nonlocal last_progress
                current = received / total if total else 0.0
                if current - last_progress >= 0.005 or current >= 1:
                    last_progress = current
                    self.events.put(("download_progress", (task.task_id, current)))
            result = self.modrinth.download_file(file, target, report)
            self.events.put(("download_done", (task.task_id, result)))
        except CatalogError as exc:
            self.events.put(("download_error", (task.task_id, str(exc))))

    def _install_selected_vanilla(self) -> None:
        selected = self.catalog_tree.selection()
        if not selected:
            messagebox.showinfo("选择原版", "请先选择一个 Minecraft 版本。")
            return
        if not self.installation:
            messagebox.showerror("未找到 PCL", "请先配置 PCL 的 .minecraft 文件夹。")
            return
        version = self.vanilla_versions[int(selected[0])]
        target = self.installation.minecraft_dir / "versions" / version.version_id
        if target.exists() and not messagebox.askyesno("版本已存在", f"{version.version_id} 已经存在。是否重新下载基础文件？"):
            return
        task = InstallTask(
            task_id=uuid.uuid4().hex[:12], name=f"Minecraft {version.version_id}",
            source="Mojang", state="安装中", detail="正在下载官方客户端与版本 JSON",
            project_type="vanilla",
        )
        self.tasks.append(task); self.task_store.save(self.tasks); self.show_page("tasks")
        threading.Thread(target=self._vanilla_install_worker, args=(task, version), daemon=True).start()

    def _vanilla_install_worker(self, task: InstallTask, version: VanillaVersion) -> None:
        try:
            def report(received: int, total: int) -> None:
                self.events.put(("install_progress", (task.task_id, received / total if total else 0.0, "正在下载官方客户端")))
            path = self.vanilla.install_base(self.installation.minecraft_dir, version, report)  # type: ignore[union-attr]
            self.events.put(("install_done", (task.task_id, path)))
        except VanillaError as exc:
            self.events.put(("install_error", (task.task_id, str(exc))))

    def _begin_mrpack_install(self, task: InstallTask) -> None:
        archive = Path(task.file_path or task.detail)
        if not archive.is_file():
            messagebox.showerror("文件不存在", "找不到已经下载的 mrpack 文件。")
            return
        if not self.installation:
            messagebox.showerror("未找到 PCL", "请先配置 PCL 的 .minecraft 文件夹。")
            return
        try:
            info = self.mrpack_installer.inspect(archive)
        except MrpackError as exc:
            messagebox.showerror("无法解析整合包", str(exc))
            return
        suggested = info["name"].strip() or archive.stem
        instance_name = simpledialog.askstring(
            "安装到 PCL",
            f"整合包：{info['name']} {info['version']}\nMinecraft：{info['minecraft']}\nFabric：{info['fabric'] or '无'}\n文件：{info['files']} 个\n\n请输入 PCL 实例名称：",
            initialvalue=suggested,
            parent=self.root,
        )
        if not instance_name:
            return
        errors = self.install_engine.validate_target(self.installation.minecraft_dir, instance_name)
        if errors:
            messagebox.showerror("无法安装", "\n".join(errors))
            return
        task.state = "部署中"; task.progress = 0; task.detail = "正在解析并部署整合包"
        task.instance_name = instance_name
        self.task_store.save(self.tasks); self._render_tasks()
        threading.Thread(target=self._mrpack_install_worker, args=(task, archive, instance_name), daemon=True).start()

    def _retry_mrpack_install(self, task: InstallTask) -> None:
        archive = Path(task.file_path)
        if not archive.is_file():
            messagebox.showerror("文件不存在", "找不到已经下载的 mrpack 文件。")
            return
        if not self.installation:
            messagebox.showerror("未找到 PCL", "请先配置 PCL 的 .minecraft 文件夹。")
            return
        if not task.instance_name:
            self._begin_mrpack_install(task)
            return
        target = self.installation.minecraft_dir / "versions" / task.instance_name
        if target.exists():
            messagebox.showerror("实例已存在", "目标实例目录已经存在，请重新选择实例名称。")
            task.instance_name = ""
            self.task_store.save(self.tasks)
            return
        task.state = "部署中"; task.progress = 0; task.detail = "正在使用已校验缓存重试部署"
        self.task_store.save(self.tasks); self._render_tasks()
        threading.Thread(target=self._mrpack_install_worker, args=(task, archive, task.instance_name), daemon=True).start()

    def _mrpack_install_worker(self, task: InstallTask, archive: Path, instance_name: str) -> None:
        try:
            def report(value: float) -> None:
                self.events.put(("install_progress", (task.task_id, value, "正在部署整合包文件")))
            path = self.mrpack_installer.install(archive, self.installation.minecraft_dir, instance_name, report)  # type: ignore[union-attr]
            self.events.put(("install_done", (task.task_id, path)))
        except MrpackError as exc:
            self.events.put(("install_error", (task.task_id, str(exc))))

    @staticmethod
    def _format_size(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{value} B"

    def _add_selected_task(self) -> None:
        project = self._selected_project()
        if not project:
            messagebox.showinfo("选择整合包", "请先选择一个整合包。")
            return
        task = InstallTask(
            task_id=uuid.uuid4().hex[:12],
            name=project.title,
            source=project.source,
            state="等待安装引擎",
            detail="已加入任务。下一阶段将接入版本选择、依赖解析与实际部署。",
        )
        self.tasks.append(task)
        self.task_store.save(self.tasks)
        self._update_stats()
        self.show_page("tasks")

    def _render_tasks(self) -> None:
        if not hasattr(self, "task_container"):
            return
        for child in self.task_container.winfo_children():
            child.destroy()
        if not self.tasks:
            empty = self._card(self.task_container, 28)
            empty.pack(fill="x")
            tk.Label(empty, text="还没有安装任务", font=(FONT, 14, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).pack(anchor="w")
            tk.Label(empty, text="前往下载中心选择一个整合包。", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"]).pack(anchor="w", pady=(6, 0))
            return
        for task in self.tasks:
            outer = tk.Frame(self.task_container, bg=COLORS["border"])
            outer.pack(fill="x", pady=(0, 10))
            card = tk.Frame(outer, bg=COLORS["surface"], padx=18, pady=14)
            card.pack(fill="x", padx=1, pady=1)
            head = tk.Frame(card, bg=COLORS["surface"])
            head.pack(fill="x")
            tk.Label(head, text=task.name, font=(FONT, 11, "bold"), fg=COLORS["text"], bg=COLORS["surface"]).pack(side="left")
            state_color = COLORS["success"] if task.state in {"下载完成", "安装完成"} else COLORS["danger"] if task.state == "失败" else COLORS["warning"]
            tk.Label(head, text=task.state, font=(FONT, 9, "bold"), fg=state_color, bg=COLORS["surface"]).pack(side="right")
            detail_row = tk.Frame(card, bg=COLORS["surface"])
            detail_row.pack(fill="x", pady=(7, 8))
            tk.Label(detail_row, text=f"{task.source} · {task.detail}", font=(FONT, 9), fg=COLORS["muted"], bg=COLORS["surface"], anchor="w", wraplength=700, justify="left").pack(side="left", fill="x", expand=True)
            possible_path = task.file_path or task.detail
            if task.state == "下载完成" and str(possible_path).casefold().endswith(".mrpack"):
                self._button(detail_row, "下一步：安装到 PCL", lambda value=task: self._begin_mrpack_install(value), width=16).pack(side="right", padx=(12, 0))
            elif task.state == "失败" and task.file_path.casefold().endswith(".mrpack"):
                self._button(detail_row, "重试部署", lambda value=task: self._retry_mrpack_install(value), width=11).pack(side="right", padx=(12, 0))
            elif task.state == "安装完成":
                self._button(detail_row, "打开实例目录", lambda value=task: os.startfile(value.detail), primary=False, width=12).pack(side="right", padx=(12, 0))
            progress = ttk.Progressbar(card, value=task.progress, maximum=1, style="App.Horizontal.TProgressbar")
            progress.pack(fill="x")

    def _clear_tasks(self) -> None:
        self.tasks = [task for task in self.tasks if task.state not in {"已取消", "失败"}]
        self.task_store.save(self.tasks)
        self._update_stats()
        self._render_tasks()

    def _launch_pcl(self) -> None:
        if not self.installation:
            messagebox.showwarning("未找到 PCL", "请先在设置中选择 PCL 程序。")
            return
        try:
            self.pcl_service.launch(self.installation)
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))

    def _open_selected_instance(self) -> None:
        selected = self.instance_tree.selection()
        if not selected:
            messagebox.showinfo("选择实例", "请先选择一个实例。")
            return
        path = self.instances[int(selected[0])].path
        os.startfile(path)

    def _choose_pcl(self) -> None:
        value = filedialog.askopenfilename(title="选择 PCL 程序", filetypes=(("PCL 程序", "*.exe"), ("全部文件", "*.*")))
        if value:
            self.pcl_path_var.set(value)

    def _choose_minecraft(self) -> None:
        value = filedialog.askdirectory(title="选择 .minecraft 文件夹")
        if value:
            self.minecraft_path_var.set(value)

    def _choose_custom_minecraft(self) -> None:
        value = filedialog.askdirectory(title="选择自定义 .minecraft 文件夹")
        if value:
            self.custom_minecraft_path_var.set(value)

    def _update_deployment_controls(self) -> None:
        if not hasattr(self, "custom_path_line"):
            return
        enabled = self.deployment_mode_var.get() == "custom"
        for child in self.custom_path_line.winfo_children():
            try:
                child.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass

    def _save_settings(self) -> None:
        pcl_path = Path(self.pcl_path_var.get().strip())
        if not pcl_path.is_file():
            messagebox.showerror("路径无效", "选择的 PCL 程序不存在。")
            return
        mode = self.deployment_mode_var.get()
        custom_path = Path(self.custom_minecraft_path_var.get().strip()) if self.custom_minecraft_path_var.get().strip() else None
        if mode == "custom":
            if not custom_path:
                messagebox.showerror("路径无效", "请选择自定义 .minecraft 文件夹。")
                return
            try:
                custom_path.mkdir(parents=True, exist_ok=True)
                (custom_path / "versions").mkdir(exist_ok=True)
            except OSError as exc:
                messagebox.showerror("无法使用目录", str(exc))
                return
        self.settings.pcl_executable = str(pcl_path)
        self.settings.deployment_mode = mode
        self.settings.custom_minecraft_dir = str(custom_path) if custom_path else ""
        self.settings.save()
        messagebox.showinfo("设置已保存", "部署目标已更新。")
        self._detect_environment()

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "environment":
                    installation, instances = payload
                    self._apply_environment(installation, instances)
                elif event == "instances":
                    self.instances = payload
                    self._update_stats()
                    self._render_instances()
                elif event == "catalog":
                    generation, projects, total = payload
                    self._render_catalog(generation, projects, total)
                elif event == "vanilla_catalog":
                    generation, versions = payload
                    self._render_vanilla_catalog(generation, versions)
                elif event == "catalog_error":
                    self._stop_loading_animation()
                    self.catalog_status.configure(text=str(payload), fg=COLORS["danger"])
                elif event == "game_versions":
                    values = tuple(["全部版本"] + list(payload))
                    self.game_version_var.set("全部版本")
                    self.game_version_combo.configure(values=values)
                elif event == "catalog_icon":
                    generation, item_id, data = payload
                    if generation == self.catalog_generation:
                        self._apply_catalog_icon(item_id, data)
                elif event == "versions":
                    project, versions = payload
                    self._show_version_dialog(project, versions)
                elif event == "download_progress":
                    task_id, progress = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.progress = progress
                        task.detail = f"正在下载 · {progress * 100:.1f}%"
                        self._render_tasks()
                elif event == "download_done":
                    task_id, path = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.state = "下载完成"
                        task.progress = 1.0
                        task.detail = str(path)
                        task.file_path = str(path)
                        self.task_store.save(self.tasks)
                        self._render_tasks()
                        messagebox.showinfo("下载完成", f"文件已经保存到：\n{path}")
                elif event == "download_error":
                    task_id, error = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.state = "失败"
                        task.detail = error
                        self.task_store.save(self.tasks)
                        self._render_tasks()
                    messagebox.showerror("下载失败", error)
                elif event == "install_progress":
                    task_id, progress, detail = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.progress = progress; task.detail = f"{detail} · {progress * 100:.1f}%"
                        self._render_tasks()
                elif event == "install_done":
                    task_id, path = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.state = "安装完成"; task.progress = 1.0; task.detail = str(path)
                        self.task_store.save(self.tasks); self._render_tasks()
                    self._refresh_instances()
                    messagebox.showinfo("安装完成", f"实例已经部署到：\n{path}\n\n回到 PCL → 版本选择 → 右键“当前文件夹”→ 刷新。")
                elif event == "install_error":
                    task_id, error = payload
                    task = next((item for item in self.tasks if item.task_id == task_id), None)
                    if task:
                        task.state = "失败"; task.detail = error
                        self.task_store.save(self.tasks); self._render_tasks()
                    messagebox.showerror("安装失败", error)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)


    @staticmethod
    def _clear_placeholder(entry: tk.Entry) -> None:
        if entry.get() in {"搜索整合包…", "输入名称或关键词…"}:
            entry.delete(0, "end")

