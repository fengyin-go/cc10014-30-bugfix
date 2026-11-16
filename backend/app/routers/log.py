"""调用日志接口：维护日志记录，覆盖开始记录、归档记录、清理日志、恢复日志、导出日志等动作。

路由顺序注意：/stats、/export、/archive、/audit、/contract 等字面路径必须注册在
/{entry_id} 之前，否则会被详情路由吞掉（例如 /api/log/export 被当成 entry_id 解析）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log import LogService

router = APIRouter(prefix="/api/log", tags=["调用日志"])

service = LogService()

LIST_FIELDS = ["记录编号", "调用服务", "请求时间", "响应耗时", "返回码", "调用方IP", "请求大小KB", "响应大小KB"]
STATUSES = ["草稿", "记录中", "已归档", "已清理"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="草稿、记录中、已归档、已清理"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤调用日志列表；同一记录编号只保留一个状态口径。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats() -> dict[str, int]:
    """统计口径：总数、记录中、已归档、已清理、待处理、归档清单数量、审计记录数。"""
    return service.stats()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出调用日志清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "log", "total": total, "items": items}


@router.get("/archive", response_model=PageResult[dict])
def list_archive(
    status: str | None = Query(default=None, description="已归档、已清理；传「已归档」即归档清单"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """调用日志归档数据集：每个记录编号一条终态记录，清理后不再计入归档清单。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_archive(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/archive/reconcile")
def reconcile_archive() -> dict[str, Any]:
    """复核核对：日志状态、归档清单、审计记录、日志归档契约快照四方口径对齐。"""
    return service.reconcile()


@router.get("/audit", response_model=PageResult[dict])
def list_audit(
    keyword: str | None = Query(default=None, description="按记录编号检索审计记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """审计记录：每次状态流转追加一条，保留原始操作详情。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_audit(keyword=keyword, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/contract")
def latest_contract() -> dict[str, Any]:
    """日志归档契约最新快照：只包含已完成校验的归档数据，供备份模块读取。"""
    snapshot = service.latest_contract()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="日志归档契约尚未发布快照")
    return snapshot


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条日志记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志记录；同一记录编号重复提交只保留一条有效记录。"""
    entry, missing, message = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条日志记录执行生命周期动作；跳级、逆向、越权动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    operator = str(payload.values.get("操作人") or payload.remark or "值班管理员").strip() or "值班管理员"
    entry, message = service.run_action(entry_id, action, operator=operator)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
