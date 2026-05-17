#!/usr/bin/env python3
"""
Generate Superset dashboard export JSON files for MIP.
These are imported by init_superset.sh into a running Superset instance.
"""
import json
import uuid
from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent

def make_uuid():
    return str(uuid.uuid4())

def base_dashboard(title: str, position: dict, filters: list = None) -> dict:
    """Create a base Superset dashboard export structure."""
    return {
        "dashboards": [
            {
                "__type__": "Dashboard",
                "__import_fields__": [
                    "dashboard_title", "position_json", "metadata",
                    "uuid", "published", "css", "json_metadata"
                ],
                "dashboard_title": title,
                "position_json": json.dumps(position),
                "css": "",
                "uuid": make_uuid(),
                "published": True,
                "metadata": {},
                "json_metadata": json.dumps({
                    "color_scheme": "supersetColors",
                    "label_colors": {},
                    "refresh_frequency": 300,
                    "timed_refresh_immune_slices": [],
                    "filter_scopes": {},
                    "cross_filters_enabled": True,
                }),
            }
        ],
        "charts": [],
        "datasets": [],
        "databases": [],
        "version": "1.0.0",
    }

def grid_position(chart_id: str, col: int, row: int, size_x: int = 6, size_y: int = 4) -> dict:
    """Generate a dashboard grid position entry."""
    return {
        "children": [],
        "id": f"CHART-{chart_id}",
        "meta": {
            "chartId": chart_id,
            "height": size_y * 30,
            "sliceName": chart_id,
            "width": size_x,
        },
        "parents": ["ROOT_ID", "GRID_ID"],
        "type": "CHART",
    }

# ── Dashboard 1: Leader Overview ─────────────────────────────────
leader_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Leader Overview",
        "uuid": make_uuid(),
        "published": True,
        "css": ".header-title { color: #003366; font-weight: bold; }",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": [
                    "ROW-header", "ROW-kpis", "ROW-charts1", "ROW-charts2"
                ],
                "id": "GRID_ID",
                "type": "GRID",
                "parents": ["ROOT_ID"]
            },
            "ROW-header": {
                "children": ["HEADER-1"],
                "id": "ROW-header",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "HEADER-1": {
                "id": "HEADER-1",
                "meta": {
                    "background": "BACKGROUND_TRANSPARENT",
                    "headerSize": "MEDIUM_HEADER",
                    "text": "Ministry Intelligence Platform – Leader Dashboard",
                },
                "parents": ["ROOT_ID", "GRID_ID", "ROW-header"],
                "type": "HEADER",
            },
            "ROW-kpis": {
                "children": ["CHART-kpi1", "CHART-kpi2", "CHART-kpi3", "CHART-kpi4"],
                "id": "ROW-kpis",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-kpi1": {
                "id": "CHART-kpi1",
                "meta": {"chartId": 1001, "height": 120, "sliceName": "This Week Present", "width": 3},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-kpis"],
                "type": "CHART",
            },
            "CHART-kpi2": {
                "id": "CHART-kpi2",
                "meta": {"chartId": 1002, "height": 120, "sliceName": "Total Members (Active)", "width": 3},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-kpis"],
                "type": "CHART",
            },
            "CHART-kpi3": {
                "id": "CHART-kpi3",
                "meta": {"chartId": 1003, "height": 120, "sliceName": "Outreaches This Month", "width": 3},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-kpis"],
                "type": "CHART",
            },
            "CHART-kpi4": {
                "id": "CHART-kpi4",
                "meta": {"chartId": 1004, "height": 120, "sliceName": "Pending Follow-Ups", "width": 3},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-kpis"],
                "type": "CHART",
            },
            "ROW-charts1": {
                "children": ["CHART-trend", "CHART-outreach"],
                "id": "ROW-charts1",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-trend": {
                "id": "CHART-trend",
                "meta": {"chartId": 1005, "height": 240, "sliceName": "Weekly Attendance Trend", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-charts1"],
                "type": "CHART",
            },
            "CHART-outreach": {
                "id": "CHART-outreach",
                "meta": {"chartId": 1006, "height": 240, "sliceName": "Outreach Count by Type", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-charts1"],
                "type": "CHART",
            },
            "ROW-charts2": {
                "children": ["CHART-activities"],
                "id": "ROW-charts2",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-activities": {
                "id": "CHART-activities",
                "meta": {"chartId": 1007, "height": 240, "sliceName": "Upcoming Activities", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-charts2"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({
            "color_scheme": "supersetColors",
            "refresh_frequency": 300,
        }),
        "metadata": {
            "rls": [],
            "native_filter_configuration": [
                {
                    "id": "NATIVE_FILTER-date",
                    "controlValues": {"enableEmptyFilter": False},
                    "name": "Date Range",
                    "filterType": "filter_time",
                    "targets": [{}],
                    "defaultValue": None,
                    "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
                    "type": "NATIVE_FILTER",
                }
            ],
        },
    }],
    "version": "1.0.0",
}

# ── Dashboard 2: Community / Outreach Map ─────────────────────────
community_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Community & Outreach Map",
        "uuid": make_uuid(),
        "published": True,
        "css": "",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": ["ROW-map", "ROW-stats"],
                "id": "GRID_ID",
                "type": "GRID",
                "parents": ["ROOT_ID"]
            },
            "ROW-map": {
                "children": ["CHART-geo"],
                "id": "ROW-map",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-geo": {
                "id": "CHART-geo",
                "meta": {"chartId": 2001, "height": 500, "sliceName": "Outreach Locations Map", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-map"],
                "type": "CHART",
            },
            "ROW-stats": {
                "children": ["CHART-people", "CHART-salvations"],
                "id": "ROW-stats",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-people": {
                "id": "CHART-people",
                "meta": {"chartId": 2002, "height": 200, "sliceName": "People Reached by Location", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-stats"],
                "type": "CHART",
            },
            "CHART-salvations": {
                "id": "CHART-salvations",
                "meta": {"chartId": 2003, "height": 200, "sliceName": "Salvations by Activity Type", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-stats"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 600}),
        "metadata": {},
    }],
    "version": "1.0.0",
}

# ── Dashboard 3: Finance ──────────────────────────────────────────
finance_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Finance Overview",
        "uuid": make_uuid(),
        "published": True,
        "css": "",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": ["ROW-fin-kpis", "ROW-fin-charts", "ROW-fin-table"],
                "id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"]
            },
            "ROW-fin-kpis": {
                "children": ["CHART-income", "CHART-expense", "CHART-balance"],
                "id": "ROW-fin-kpis",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-income": {
                "id": "CHART-income",
                "meta": {"chartId": 3001, "height": 120, "sliceName": "Total Income (Month)", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-kpis"],
                "type": "CHART",
            },
            "CHART-expense": {
                "id": "CHART-expense",
                "meta": {"chartId": 3002, "height": 120, "sliceName": "Total Expenses (Month)", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-kpis"],
                "type": "CHART",
            },
            "CHART-balance": {
                "id": "CHART-balance",
                "meta": {"chartId": 3003, "height": 120, "sliceName": "Net Balance", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-kpis"],
                "type": "CHART",
            },
            "ROW-fin-charts": {
                "children": ["CHART-inc-exp", "CHART-catpie"],
                "id": "ROW-fin-charts",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-inc-exp": {
                "id": "CHART-inc-exp",
                "meta": {"chartId": 3004, "height": 300, "sliceName": "Income vs Expense (Monthly)", "width": 7},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-charts"],
                "type": "CHART",
            },
            "CHART-catpie": {
                "id": "CHART-catpie",
                "meta": {"chartId": 3005, "height": 300, "sliceName": "Spending by Category", "width": 5},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-charts"],
                "type": "CHART",
            },
            "ROW-fin-table": {
                "children": ["CHART-fin-table"],
                "id": "ROW-fin-table",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-fin-table": {
                "id": "CHART-fin-table",
                "meta": {"chartId": 3006, "height": 300, "sliceName": "Recent Transactions", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-fin-table"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 300}),
        "metadata": {},
    }],
    "version": "1.0.0",
}

# ── Dashboard 4: Donations ────────────────────────────────────────
donations_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Donations & Giving",
        "uuid": make_uuid(),
        "published": True,
        "css": "",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": ["ROW-don-kpis", "ROW-don-charts", "ROW-don-table"],
                "id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"]
            },
            "ROW-don-kpis": {
                "children": ["CHART-total-don", "CHART-pending-don", "CHART-donors"],
                "id": "ROW-don-kpis",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-total-don": {
                "id": "CHART-total-don",
                "meta": {"chartId": 4001, "height": 120, "sliceName": "Total Confirmed Donations", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-kpis"],
                "type": "CHART",
            },
            "CHART-pending-don": {
                "id": "CHART-pending-don",
                "meta": {"chartId": 4002, "height": 120, "sliceName": "Pending Donations", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-kpis"],
                "type": "CHART",
            },
            "CHART-donors": {
                "id": "CHART-donors",
                "meta": {"chartId": 4003, "height": 120, "sliceName": "Unique Donors", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-kpis"],
                "type": "CHART",
            },
            "ROW-don-charts": {
                "children": ["CHART-cat-bar", "CHART-method-pie"],
                "id": "ROW-don-charts",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-cat-bar": {
                "id": "CHART-cat-bar",
                "meta": {"chartId": 4004, "height": 300, "sliceName": "Donations by Category", "width": 7},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-charts"],
                "type": "CHART",
            },
            "CHART-method-pie": {
                "id": "CHART-method-pie",
                "meta": {"chartId": 4005, "height": 300, "sliceName": "Payment Method Split", "width": 5},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-charts"],
                "type": "CHART",
            },
            "ROW-don-table": {
                "children": ["CHART-don-table"],
                "id": "ROW-don-table",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-don-table": {
                "id": "CHART-don-table",
                "meta": {"chartId": 4006, "height": 300, "sliceName": "Recent Donations", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-don-table"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 180}),
        "metadata": {},
    }],
    "version": "1.0.0",
}

# ── Dashboard 5: Team Scorecard (RLS-protected) ───────────────────
scorecard_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Team Scorecard",
        "uuid": make_uuid(),
        "published": True,
        "css": "",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": ["ROW-sc-kpis", "ROW-sc-charts", "ROW-sc-followup"],
                "id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"]
            },
            "ROW-sc-kpis": {
                "children": ["CHART-sc-att", "CHART-sc-out", "CHART-sc-follow"],
                "id": "ROW-sc-kpis",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-sc-att": {
                "id": "CHART-sc-att",
                "meta": {"chartId": 5001, "height": 120, "sliceName": "Team Attendance Rate", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-kpis"],
                "type": "CHART",
            },
            "CHART-sc-out": {
                "id": "CHART-sc-out",
                "meta": {"chartId": 5002, "height": 120, "sliceName": "Outreach Participation", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-kpis"],
                "type": "CHART",
            },
            "CHART-sc-follow": {
                "id": "CHART-sc-follow",
                "meta": {"chartId": 5003, "height": 120, "sliceName": "Follow-Up Completion %", "width": 4},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-kpis"],
                "type": "CHART",
            },
            "ROW-sc-charts": {
                "children": ["CHART-sc-trend", "CHART-sc-heatmap"],
                "id": "ROW-sc-charts",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-sc-trend": {
                "id": "CHART-sc-trend",
                "meta": {"chartId": 5004, "height": 300, "sliceName": "Attendance Trend (Team)", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-charts"],
                "type": "CHART",
            },
            "CHART-sc-heatmap": {
                "id": "CHART-sc-heatmap",
                "meta": {"chartId": 5005, "height": 300, "sliceName": "Member Consistency Heatmap", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-charts"],
                "type": "CHART",
            },
            "ROW-sc-followup": {
                "children": ["CHART-sc-fl-table"],
                "id": "ROW-sc-followup",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-sc-fl-table": {
                "id": "CHART-sc-fl-table",
                "meta": {"chartId": 5006, "height": 300, "sliceName": "Follow-Up List (Your Team)", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-sc-followup"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 300}),
        "metadata": {"rls_rules": ["RLS_attendance_team", "RLS_outreaches_team", "RLS_follow_ups_team"]},
    }],
    "version": "1.0.0",
}

# ── Dashboard 6: Goals ─────────────────────────────────────────────
goals_dashboard = {
    "dashboards": [{
        "__type__": "Dashboard",
        "dashboard_title": "MIP – Semester Goals",
        "uuid": make_uuid(),
        "published": True,
        "css": "",
        "position_json": json.dumps({
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {
                "children": ["ROW-goals-header", "ROW-goals-bars", "ROW-goals-table"],
                "id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"]
            },
            "ROW-goals-header": {
                "children": ["HEADER-goals"],
                "id": "ROW-goals-header",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "HEADER-goals": {
                "id": "HEADER-goals",
                "meta": {
                    "background": "BACKGROUND_TRANSPARENT",
                    "headerSize": "MEDIUM_HEADER",
                    "text": "Semester 2026-S1 Goals Progress",
                },
                "parents": ["ROOT_ID", "GRID_ID", "ROW-goals-header"],
                "type": "HEADER",
            },
            "ROW-goals-bars": {
                "children": ["CHART-goals-progress"],
                "id": "ROW-goals-bars",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-goals-progress": {
                "id": "CHART-goals-progress",
                "meta": {"chartId": 6001, "height": 400, "sliceName": "Goals Progress Bars", "width": 12},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-goals-bars"],
                "type": "CHART",
            },
            "ROW-goals-table": {
                "children": ["CHART-goals-table", "CHART-goals-vs"],
                "id": "ROW-goals-table",
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "parents": ["ROOT_ID", "GRID_ID"],
                "type": "ROW",
            },
            "CHART-goals-table": {
                "id": "CHART-goals-table",
                "meta": {"chartId": 6002, "height": 300, "sliceName": "Goals Detail Table", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-goals-table"],
                "type": "CHART",
            },
            "CHART-goals-vs": {
                "id": "CHART-goals-vs",
                "meta": {"chartId": 6003, "height": 300, "sliceName": "Actual vs Target (Bar)", "width": 6},
                "parents": ["ROOT_ID", "GRID_ID", "ROW-goals-table"],
                "type": "CHART",
            },
        }),
        "json_metadata": json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 3600}),
        "metadata": {},
    }],
    "version": "1.0.0",
}

# ── Save all dashboards ────────────────────────────────────────────
dashboards = {
    "export_leader.json":     leader_dashboard,
    "export_community.json":  community_dashboard,
    "export_finance.json":    finance_dashboard,
    "export_donations.json":  donations_dashboard,
    "export_team_scorecard.json": scorecard_dashboard,
    "export_goals.json":      goals_dashboard,
}

for filename, data in dashboards.items():
    path = DASHBOARD_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Generated: {path}")

print(f"\nAll {len(dashboards)} dashboard JSON files generated successfully.")
