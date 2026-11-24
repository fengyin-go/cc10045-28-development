"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.realtime_agg import RealtimeAggService
from app.services.event_tracker import EventTrackerService
from app.services.dashboard_builder import DashboardBuilderService
from app.services.query_cache import QueryCacheService
from app.services.segment_engine import SegmentEngineService
from app.services.metric_store import MetricStoreService
from app.services.drill_path import DrillPathService
from app.services.alert_policy import AlertPolicyService
from app.services.data_report import DataReportService
from app.services.anomaly_analysis import AnomalyAnalysisService
from app.services.forecast_model import ForecastModelService
from app.services.cohort_compare import CohortCompareService
from app.services.data_export import DataExportService
from app.services.data_subscription import DataSubscriptionService
from app.services.query_history import QueryHistoryService
from app.services.report_snapshot import ReportSnapshotService
from app.services.data_api import DataApiService
from app.services.retention_analysis import RetentionAnalysisService
from app.services.funnel_analyzer import FunnelAnalyzerService
from app.services.cube_builder import CubeBuilderService
from app.services.run_delegation import run_delegation_service

SERVICES: dict[str, BaseModuleService] = {
    "realtime_agg": RealtimeAggService(),
    "event_tracker": EventTrackerService(),
    "dashboard_builder": DashboardBuilderService(),
    "query_cache": QueryCacheService(),
    "segment_engine": SegmentEngineService(),
    "metric_store": MetricStoreService(),
    "drill_path": DrillPathService(),
    "alert_policy": AlertPolicyService(),
    "data_report": DataReportService(),
    "anomaly_analysis": AnomalyAnalysisService(),
    "forecast_model": ForecastModelService(),
    "cohort_compare": CohortCompareService(),
    "data_export": DataExportService(),
    "data_subscription": DataSubscriptionService(),
    "query_history": QueryHistoryService(),
    "report_snapshot": ReportSnapshotService(),
    "data_api": DataApiService(),
    "retention_analysis": RetentionAnalysisService(),
    "funnel_analyzer": FunnelAnalyzerService(),
    "cube_builder": CubeBuilderService(),
    "run_delegation": run_delegation_service,
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
