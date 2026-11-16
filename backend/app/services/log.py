"""调用日志业务规则：状态流转、字段校验与筛选口径都收在这里。

生命周期（单向、逐级、不可跳步）：

    草稿 → 记录中 → 已归档 → 已清理

恢复同样只允许单步回退：已清理 → 已归档 → 记录中。

每一次真实的状态流转都会在同一调用里同步四件事，保证清单、数量与审计口径一致：

1. 日志记录本身的 ``status`` / ``pending``（待处理口径：草稿、记录中）；
2. 调用日志归档数据集（每个记录编号只保留一条终态记录，清理后不再计入归档清单）；
3. 审计记录（追加原始操作详情，可按记录编号复核每一次流转）；
4. 「日志归档契约」快照（只收已完成校验的归档数据，供备份模块读取）。

冲突仲裁不依赖录入顺序：同一记录编号出现多个状态时，生命周期位次高者优先
（已清理 > 已归档 > 记录中 > 草稿），位次相同则保留编号最小的一条，结果与到达先后无关。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "log"
REQUIRED_FIELDS = ["记录编号", "调用服务", "请求时间"]
STATUS_ORDER = ["草稿", "记录中", "已归档", "已清理"]
PENDING_STATUSES = {"草稿", "记录中"}

# 允许的单步流转边：动作 → [(起始状态, 目标状态)]；不在边上的流转一律拒绝（跳级/逆向）。
ACTION_EDGES: dict[str, list[tuple[str, str]]] = {
    "开始记录": [("草稿", "记录中")],
    "归档记录": [("记录中", "已归档")],
    "清理日志": [("已归档", "已清理")],
    "恢复日志": [("已清理", "已归档"), ("已归档", "记录中")],
}
# 只读动作：允许执行但不改变状态、不进审计。
READONLY_ACTIONS = {"导出日志"}

ARCHIVE_LEDGER = "log_archive"  # 调用日志归档数据集
AUDIT_LEDGER = "log_audit"  # 审计记录
CONTRACT_LEDGER = "log_archive_contract"  # 日志归档契约（备份模块只读这里的快照）
CONTRACT_NAME = "日志归档契约"


class LogService:
    def __init__(self) -> None:
        self._reconcile_existing()

    # ------------------------------------------------------------------ 口径
    @staticmethod
    def _rank(status: Any) -> int:
        """生命周期位次；未知状态排在最前。"""
        try:
            return STATUS_ORDER.index(str(status))
        except ValueError:
            return -1

    @staticmethod
    def _pending_of(status: Any) -> bool:
        """待处理口径：只有草稿、记录中计入待处理；已归档、已清理不计。"""
        return status in PENDING_STATUSES

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ---------------------------------------------------------------- 启动对账
    def _reconcile_existing(self) -> None:
        """启动时把已有数据对齐到当前口径（幂等，可重复执行）。

        - 逐条校正 pending，消除历史 drift；
        - 已归档但不在归档数据集里的记录补入数据集，并补一条「初始同步」审计，
          保证归档清单数量与审计记录从起点就一致；
        - 发布首版日志归档契约快照。
        """
        dataset = store.ledger(ARCHIVE_LEDGER)
        audit = store.ledger(AUDIT_LEDGER)
        changed = False
        for row in store.rows(MODULE):
            expected = self._pending_of(row.get("status"))
            if bool(row.get("pending")) != expected:
                row["pending"] = expected
                changed = True
            if row.get("status") != "已归档":
                continue
            code = str(row.get("记录编号") or "")
            if not code or any(r.get("记录编号") == code for r in dataset):
                continue
            dataset.append(self._archive_row(row, archived_at=self._now()))
            changed = True
            if not any(a.get("记录编号") == code for a in audit):
                self._append_audit(
                    row,
                    action="初始同步",
                    from_status="记录中",
                    to_status="已归档",
                    operator="系统",
                    detail=f"启动对账：{code} 已处于已归档，补入归档数据集",
                )
        if changed or not store.ledger(CONTRACT_LEDGER):
            self._publish_contract()

    # ---------------------------------------------------------------- 查询链
    def _dedupe(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同一记录编号只保留一条：生命周期位次高者优先，位次相同取 id 小者。

        迟到日志与已归档记录同时出现时，清单只保留一个状态和一个数量口径；
        仲裁只看生命周期位次，与录入顺序无关。
        """
        best: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("记录编号") or f"__id_{row.get('id')}")
            current = best.get(key)
            if current is None:
                best[key] = row
                continue
            row_key = (self._rank(row.get("status")), -int(row.get("id", 0)))
            cur_key = (self._rank(current.get("status")), -int(current.get("id", 0)))
            if row_key > cur_key:
                best[key] = row
        return sorted(best.values(), key=lambda row: int(row.get("id", 0)))

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._dedupe(store.rows(MODULE))
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            # 详情页与列表页共用同一口径，避免详情页残留待处理。
            entry["pending"] = self._pending_of(entry.get("status"))
        return entry

    def stats(self) -> dict[str, int]:
        rows = self._dedupe(store.rows(MODULE))
        dataset = store.ledger(ARCHIVE_LEDGER)
        return {
            "日志记录总数": len(rows),
            "记录中日志": sum(1 for row in rows if row.get("status") == "记录中"),
            "已归档记录": sum(1 for row in rows if row.get("status") == "已归档"),
            "已清理记录": sum(1 for row in rows if row.get("status") == "已清理"),
            "待处理": sum(1 for row in rows if self._pending_of(row.get("status"))),
            "归档清单数量": sum(1 for row in dataset if row.get("status") == "已归档"),
            "审计记录数": len(store.ledger(AUDIT_LEDGER)),
        }

    # ---------------------------------------------------------------- 登记
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        code = str(values.get("记录编号") or "").strip()
        existing = self._find_by_code(code)
        if existing is not None:
            # 同一页面连续提交两次 / 迟到日志：只保留一条有效记录，
            # 既有记录的生命周期位次不低于新提交（新提交只能是草稿），直接合并。
            return (
                existing,
                [],
                f"记录编号 {code} 已存在（当前状态：{existing.get('status')}），重复提交已合并为一条有效记录",
            )
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = self._pending_of(entry["status"])
        entry["abnormal"] = False
        rows.append(entry)
        return entry, [], "日志记录已登记"

    def _find_by_code(self, code: str) -> dict[str, Any] | None:
        candidates = [row for row in store.rows(MODULE) if str(row.get("记录编号") or "") == code]
        if not candidates:
            return None
        # 仲裁不依赖录入顺序：位次高者优先，位次相同取 id 小者。
        return max(
            candidates,
            key=lambda row: (self._rank(row.get("status")), -int(row.get("id", 0))),
        )

    # ---------------------------------------------------------------- 动作
    def run_action(
        self, entry_id: int, action: str, *, operator: str = "值班管理员"
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志记录 {entry_id} 不存在或已归档"
        action = (action or "").strip()
        if action in READONLY_ACTIONS:
            return entry, f"日志记录已完成「{action}」，状态保持{entry.get('status')}不变"
        edges = ACTION_EDGES.get(action)
        if not edges:
            return None, f"动作「{action}」不属于调用日志可执行范围"
        current = str(entry.get("status") or "")
        edge = next((item for item in edges if item[0] == current), None)
        if edge is None:
            if any(current == target for _, target in edges):
                # 幂等合并：重复归档/清理只保留一个终态记录，不重复入账、不重复审计。
                if action == "恢复日志":
                    return entry, f"日志记录当前为{current}，无需恢复"
                if action in ("归档记录", "清理日志"):
                    return entry, f"日志记录已处于{current}，重复「{action}」已合并，仅保留一条终态记录"
                return entry, f"日志记录已处于{current}，重复「{action}」已合并，仅保留一条有效记录"
            expected = "、".join(dict.fromkeys(from_status for from_status, _ in edges))
            return None, (
                f"日志记录当前为{current}，{action}只能从{expected}单步流转，"
                "不允许跳级归档或逆向操作"
            )
        from_status, target = edge
        entry["status"] = target
        entry["pending"] = self._pending_of(target)
        entry["abnormal"] = False
        # 同一调用内同步：状态 → 归档数据集 → 审计记录 → 日志归档契约快照。
        self._sync_dataset(entry, target)
        self._append_audit(
            entry,
            action=action,
            from_status=from_status,
            to_status=target,
            operator=operator,
            detail=f"{action}：{entry.get('记录编号')} {from_status} → {target}（操作人：{operator}）",
        )
        self._publish_contract()
        return entry, f"日志记录已完成「{action}」：{from_status} → {target}"

    # ------------------------------------------------------------ 归档数据集
    @staticmethod
    def _archive_row(entry: dict[str, Any], *, archived_at: str) -> dict[str, Any]:
        dataset = store.ledger(ARCHIVE_LEDGER)
        return {
            "id": max((int(row.get("id", 0)) for row in dataset), default=0) + 1,
            "记录编号": entry.get("记录编号"),
            "log_id": entry.get("id"),
            "调用服务": entry.get("调用服务"),
            "请求时间": entry.get("请求时间"),
            "status": "已归档",
            "pending": False,
            "归档时间": archived_at,
            "归档窗口": archived_at[:10],
            "清理时间": None,
        }

    def _sync_dataset(self, entry: dict[str, Any], target: str) -> None:
        """归档数据集随状态同步切换；同一记录编号只保留一条终态记录。"""
        dataset = store.ledger(ARCHIVE_LEDGER)
        code = str(entry.get("记录编号") or "")
        row = next((item for item in dataset if item.get("记录编号") == code), None)
        now = self._now()
        if target == "已归档":
            if row is None:
                dataset.append(self._archive_row(entry, archived_at=now))
            else:
                # 清理后恢复回已归档：复用同一记录，清单仍只算一条。
                row["status"] = "已归档"
                row["pending"] = False
                row["log_id"] = entry.get("id")
                row["归档时间"] = now
                row["归档窗口"] = now[:10]
                row["清理时间"] = None
        elif target == "已清理":
            if row is not None:
                # 同步清理：清单不再把该记录计入已归档，避免清单残留已清理记录。
                row["status"] = "已清理"
                row["pending"] = False
                row["清理时间"] = now
        elif target == "记录中" and row is not None:
            # 恢复出归档：记录回到处理环节，从归档数据集中移除（审计保留历史）。
            dataset.remove(row)

    def list_archive(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """调用日志归档数据集；status=已归档 时即归档清单（不含已清理记录）。"""
        rows = list(store.ledger(ARCHIVE_LEDGER))
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: (str(row.get("归档时间") or ""), str(row.get("记录编号") or "")))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ---------------------------------------------------------------- 审计
    def _append_audit(
        self,
        entry: dict[str, Any],
        *,
        action: str,
        from_status: str,
        to_status: str,
        operator: str,
        detail: str,
    ) -> dict[str, Any]:
        audit = store.ledger(AUDIT_LEDGER)
        record = {
            "seq": len(audit) + 1,
            "记录编号": entry.get("记录编号"),
            "log_id": entry.get("id"),
            "动作": action,
            "原状态": from_status,
            "新状态": to_status,
            "操作人": operator,
            "时间": self._now(),
            "详情": detail,
        }
        audit.append(record)
        return record

    def list_audit(
        self,
        *,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.ledger(AUDIT_LEDGER))
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        rows.sort(key=lambda row: int(row.get("seq", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # -------------------------------------------------------- 日志归档契约
    def _publish_contract(self) -> dict[str, Any]:
        """把当前归档清单发布为「日志归档契约」快照。

        快照只收已完成校验的记录：状态为已归档，且源日志记录同样处于已归档。
        备份模块只读取这里的快照，不直接读日志表。
        """
        validated: list[dict[str, Any]] = []
        for row in store.ledger(ARCHIVE_LEDGER):
            if row.get("status") != "已归档":
                continue
            source = store.find(MODULE, int(row.get("log_id", 0) or 0))
            if source is None or source.get("status") != "已归档":
                continue
            validated.append(
                {
                    "记录编号": row.get("记录编号"),
                    "log_id": row.get("log_id"),
                    "status": row.get("status"),
                    "归档时间": row.get("归档时间"),
                    "归档窗口": row.get("归档窗口"),
                }
            )
        validated.sort(key=lambda item: str(item.get("记录编号") or ""))
        contract = store.ledger(CONTRACT_LEDGER)
        snapshot = {
            "契约名称": CONTRACT_NAME,
            "版本": len(contract) + 1,
            "生成时间": self._now(),
            "校验状态": "已校验",
            "已归档数量": len(validated),
            "快照": validated,
        }
        contract.append(snapshot)
        return snapshot

    def latest_contract(self) -> dict[str, Any] | None:
        contract = store.ledger(CONTRACT_LEDGER)
        return contract[-1] if contract else None

    # ---------------------------------------------------------------- 复核
    def reconcile(self) -> dict[str, Any]:
        """复核核对：日志状态、归档清单、审计记录、契约快照四方口径对齐。"""
        rows = self._dedupe(store.rows(MODULE))
        dataset = store.ledger(ARCHIVE_LEDGER)
        audit = store.ledger(AUDIT_LEDGER)
        differences: list[str] = []

        log_archived = {str(row.get("记录编号")) for row in rows if row.get("status") == "已归档"}
        dataset_archived = {str(row.get("记录编号")) for row in dataset if row.get("status") == "已归档"}
        if log_archived != dataset_archived:
            only_log = sorted(log_archived - dataset_archived)
            only_dataset = sorted(dataset_archived - log_archived)
            if only_log:
                differences.append(f"日志已归档但归档数据集缺失：{'、'.join(only_log)}")
            if only_dataset:
                differences.append(f"归档数据集残留非已归档记录：{'、'.join(only_dataset)}")

        latest_by_code: dict[str, dict[str, Any]] = {}
        for record in audit:
            latest_by_code[str(record.get("记录编号"))] = record
        audit_archived = {
            code for code, record in latest_by_code.items() if record.get("新状态") == "已归档"
        }
        missing_audit = sorted(dataset_archived - audit_archived)
        if missing_audit:
            differences.append(f"归档清单缺少可复核的审计记录：{'、'.join(missing_audit)}")

        latest = self.latest_contract()
        contract_count = int(latest.get("已归档数量", 0)) if latest else 0
        if latest is None:
            differences.append("日志归档契约尚未发布快照")
        elif contract_count != len(dataset_archived):
            differences.append(
                f"契约快照已归档数量（{contract_count}）与归档清单（{len(dataset_archived)}）不一致"
            )

        pending_drift = [
            str(row.get("记录编号"))
            for row in rows
            if bool(row.get("pending")) != self._pending_of(row.get("status"))
        ]
        if pending_drift:
            differences.append(f"待处理口径漂移：{'、'.join(pending_drift)}")

        return {
            "consistent": not differences,
            "日志已归档数量": len(log_archived),
            "归档清单数量": len(dataset_archived),
            "审计已归档数量": len(audit_archived & dataset_archived),
            "契约快照已归档数量": contract_count,
            "待处理数量": sum(1 for row in rows if self._pending_of(row.get("status"))),
            "差异": differences,
        }
