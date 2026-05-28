#!/usr/bin/env python3
"""
致城酒店投资测算系统 — 后端服务
FastAPI + SQLite，支持多人协同录入、客资认领、测算引擎、PDF导出
启动方式: python app.py
"""

import os
import json
import sqlite3
import uuid
import socket
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ─── 配置 ───────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "zhicheng.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
HOST = "0.0.0.0"
PORT = 8088

app = FastAPI(title="致城酒店投资测算系统", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── 数据库 ─────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                hotel_name TEXT NOT NULL,
                contact_name TEXT DEFAULT '',
                contact_phone TEXT DEFAULT '',
                source TEXT DEFAULT '',
                area REAL DEFAULT 0,
                rooms INTEGER DEFAULT 0,
                hotel_level TEXT DEFAULT '经济型',
                hotel_type TEXT DEFAULT '连锁型',
                address TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT '待处理',
                created_by TEXT NOT NULL,
                created_by_name TEXT DEFAULT '',
                claimed_by TEXT DEFAULT '',
                claimed_by_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS calculations (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                params_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_by TEXT DEFAULT '',
                created_by_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (client_id) REFERENCES clients(id)
            );
        """)


# ─── 工具函数 ───────────────────────────────────────
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def calc_engine(params: dict) -> dict:
    """核心测算引擎 — 与 PDF 中逻辑完全对齐"""
    try:
        # 解参
        room_count = float(params.get("roomCount", 0))
        total_area = float(params.get("totalArea", 0))
        invest_budget = float(params.get("investBudget", 0))
        transfer_budget = float(params.get("transferBudget", 0))
        deposit_budget = float(params.get("depositBudget", 0))
        startup_budget = float(params.get("startupBudget", 0))
        reno_budget = float(params.get("renoBudget", 0))
        sqm_rent = float(params.get("sqmRent", 0))
        property_fee = float(params.get("propertyFee", 0))
        rent_ratio_pct = float(params.get("rentRatio", 0))
        rent_base = float(params.get("rentBase", 0))
        peak_m = float(params.get("peakMonths", 0))
        peak_adr = float(params.get("peakADR", 0))
        peak_occ = float(params.get("peakOCC", 0))
        mid_m = float(params.get("midMonths", 0))
        mid_adr = float(params.get("midADR", 0))
        mid_occ = float(params.get("midOCC", 0))
        low_m = float(params.get("lowMonths", 0))
        low_adr = float(params.get("lowADR", 0))
        low_occ = float(params.get("lowOCC", 0))
        discount_rate = float(params.get("discountRate", 85))
        yearly_marginal = float(params.get("yearlyMarginalCost", 0))
        staff_count = float(params.get("staffCount", 0))
        salary_per = float(params.get("salaryPerStaff", 0))
        fund_retention = float(params.get("fundRetention", 5))
        mgmt_fee_rate = float(params.get("mgmtFeeRate", 2))
        dividend_ratio = float(params.get("dividendRatio", 100))

        if room_count <= 0:
            return {"error": "客房数量必须大于0"}

        # --- 租金测算 ---
        year_fixed_rent = total_area * (sqm_rent + property_fee) * 12
        year_float_rent = rent_base * rent_ratio_pct / 100 if rent_base > 0 else 0
        year_total_rent = year_fixed_rent + year_float_rent
        month_rent = year_total_rent / 12
        day_rent = year_total_rent / 365
        room_day_rent = year_total_rent / room_count / 365

        # --- 三季收入 ---
        peak_rev = peak_m * 30 * room_count * peak_adr * peak_occ / 100
        mid_rev = mid_m * 30 * room_count * mid_adr * mid_occ / 100
        low_rev = low_m * 30 * room_count * low_adr * low_occ / 100
        year_rev_stable = peak_rev + mid_rev + low_rev
        year_rev_cons = year_rev_stable * discount_rate / 100

        peak_rp = peak_adr * peak_occ / 100
        mid_rp = mid_adr * mid_occ / 100
        low_rp = low_adr * low_occ / 100
        year_rp_stable = (peak_rp * peak_m + mid_rp * mid_m + low_rp * low_m) / 12
        year_rp_cons = year_rp_stable * discount_rate / 100

        year_adr_stable = (peak_adr * peak_m + mid_adr * mid_m + low_adr * low_m) / 12
        year_adr_cons = year_adr_stable * discount_rate / 100

        year_occ_stable = (peak_occ * peak_m + mid_occ * mid_m + low_occ * low_m) / 12

        day_rev_stable = year_rev_stable / 365
        day_rev_cons = year_rev_cons / 365

        peak_rn = peak_m * 30 * room_count * peak_occ / 100
        mid_rn = mid_m * 30 * room_count * mid_occ / 100
        low_rn = low_m * 30 * room_count * low_occ / 100
        year_rn_stable = peak_rn + mid_rn + low_rn
        year_rn_cons = year_rn_stable * discount_rate / 100

        # --- 成本 ---
        year_salary = staff_count * salary_per
        night_rent_cost = year_total_rent / year_rn_stable if year_rn_stable > 0 else 0
        night_marginal = yearly_marginal / year_rn_stable if year_rn_stable > 0 else 0
        night_labor = year_salary / year_rn_stable if year_rn_stable > 0 else 0
        night_total = night_rent_cost + night_marginal + night_labor

        year_total_cost_stable = year_total_rent + yearly_marginal + year_salary
        gop_stable = year_rev_stable - year_total_cost_stable
        gop_rate_stable = (gop_stable / year_rev_stable * 100) if year_rev_stable > 0 else 0
        be_rp_stable = night_total
        be_month_rev_stable = night_total * room_count * 30
        be_occ_stable = (night_total / year_adr_stable * 100) if year_adr_stable > 0 else 0

        year_total_cost_cons = year_total_rent + yearly_marginal * discount_rate / 100 + year_salary * discount_rate / 100
        gop_cons = year_rev_cons - year_total_cost_cons
        gop_rate_cons = (gop_cons / year_rev_cons * 100) if year_rev_cons > 0 else 0
        be_rp_cons = year_total_cost_cons / year_rn_cons if year_rn_cons > 0 else 0
        be_occ_cons = (be_rp_cons / year_adr_cons * 100) if year_adr_cons > 0 else 0

        # --- 决策 ---
        fund_stable = year_rev_stable * fund_retention / 100
        fund_cons = year_rev_cons * fund_retention / 100

        mgmt_fixed_stable = year_rev_stable * mgmt_fee_rate / 100
        mgmt_fixed_cons = year_rev_cons * mgmt_fee_rate / 100
        mgmt_reward_stable = gop_stable * 0.05 if gop_stable > 0 else 0
        mgmt_reward_cons = gop_cons * 0.05 if gop_cons > 0 else 0
        mgmt_total_stable = mgmt_fixed_stable + mgmt_reward_stable
        mgmt_total_cons = mgmt_fixed_cons + mgmt_reward_cons

        net_stable = (year_rev_stable - year_total_cost_stable - mgmt_total_stable) * dividend_ratio / 100
        net_cons = (year_rev_cons - year_total_cost_cons - mgmt_total_cons) * dividend_ratio / 100

        roi_stable = invest_budget / net_stable if net_stable > 0 else 999
        roi_cons = invest_budget / net_cons if net_cons > 0 else 999

        rent_pct_stable = (year_total_rent / year_rev_stable * 100) if year_rev_stable > 0 else 0
        rent_pct_cons = (year_total_rent / year_rev_cons * 100) if year_rev_cons > 0 else 0

        # 投资建议
        if roi_stable < 2.5 and gop_rate_stable > 30:
            verdict = "建议投资"
            verdict_color = "#2d6a5e"
        elif roi_stable < 5 and gop_rate_stable > 15:
            verdict = "谨慎投资"
            verdict_color = "#b8963e"
        else:
            verdict = "不建议投资"
            verdict_color = "#7a2e3f"

        return {
            "hotelName": params.get("hotelName", ""),
            "contactName": params.get("contactName", ""),
            "contactPhone": params.get("contactPhone", ""),
            "calcTime": now_str(),
            # 租金
            "yearFixedRent": round(year_fixed_rent, 2),
            "yearFloatRent": round(year_float_rent, 2),
            "yearTotalRent": round(year_total_rent, 2),
            "monthRent": round(month_rent, 2),
            "dayRent": round(day_rent, 2),
            "roomDayRent": round(room_day_rent, 2),
            # 收入
            "peakRevenue": round(peak_rev, 2),
            "midRevenue": round(mid_rev, 2),
            "lowRevenue": round(low_rev, 2),
            "yearRevenue": {
                "stable": round(year_rev_stable, 2),
                "conservative": round(year_rev_cons, 2),
            },
            "yearRevPar": {
                "stable": round(year_rp_stable, 2),
                "conservative": round(year_rp_cons, 2),
            },
            "yearADR": {
                "stable": round(year_adr_stable, 2),
                "conservative": round(year_adr_cons, 2),
            },
            "yearOCC": round(year_occ_stable, 1),
            "dayRevenue": {
                "stable": round(day_rev_stable, 2),
                "conservative": round(day_rev_cons, 2),
            },
            "yearRoomNights": {
                "stable": round(year_rn_stable, 0),
                "conservative": round(year_rn_cons, 0),
            },
            # 成本
            "nightRentCost": round(night_rent_cost, 2),
            "nightMarginalCost": round(night_marginal, 2),
            "nightLaborCost": round(night_labor, 2),
            "nightTotalCost": round(night_total, 2),
            "yearTotalCost": {
                "stable": round(year_total_cost_stable, 2),
                "conservative": round(year_total_cost_cons, 2),
            },
            "yearSalary": round(year_salary, 2),
            # GOP
            "gopGross": {
                "stable": round(gop_stable, 2),
                "conservative": round(gop_cons, 2),
            },
            "gopRate": {
                "stable": round(gop_rate_stable, 1),
                "conservative": round(gop_rate_cons, 1),
            },
            "beRevPar": {
                "stable": round(be_rp_stable, 2),
                "conservative": round(be_rp_cons, 2),
            },
            "beMonthRevenue_stable": round(be_month_rev_stable, 2),
            "beOCC": {
                "stable": round(be_occ_stable, 1),
                "conservative": round(be_occ_cons, 1),
            },
            # 决策
            "fundReserve": {
                "stable": round(fund_stable, 2),
                "conservative": round(fund_cons, 2),
            },
            "mgmtFee": {
                "stable": round(mgmt_total_stable, 2),
                "conservative": round(mgmt_total_cons, 2),
            },
            "netProfit": {
                "stable": round(net_stable, 2),
                "conservative": round(net_cons, 2),
            },
            "monthNetProfit": {
                "stable": round(net_stable / 12, 2),
                "conservative": round(net_cons / 12, 2),
            },
            "roiYears": {
                "stable": round(roi_stable, 1),
                "conservative": round(roi_cons, 1),
            },
            "rentRatio": {
                "stable": round(rent_pct_stable, 1),
                "conservative": round(rent_pct_cons, 1),
            },
            "verdict": verdict,
            "verdictColor": verdict_color,
            "investBudget": round(invest_budget, 2),
            "transferBudget": round(transfer_budget, 2),
            "depositBudget": round(deposit_budget, 2),
            "startupBudget": round(startup_budget, 2),
            "renoBudget": round(reno_budget, 2),
        }
    except Exception as e:
        return {"error": f"测算异常: {str(e)}"}


# ─── API ────────────────────────────────────────────

@app.get("/api/ping")
def ping():
    return {"status": "ok", "time": now_str(), "ip": get_local_ip()}


# 用户
@app.post("/api/users")
def create_user(data: dict):
    uid = str(uuid.uuid4())[:8]
    name = data.get("name", "匿名用户").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    with get_db() as db:
        db.execute("INSERT INTO users (id,name) VALUES (?,?)", (uid, name))
    return {"id": uid, "name": name}


@app.get("/api/users")
def list_users():
    with get_db() as db:
        rows = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# 客资
@app.get("/api/clients")
def list_clients(status: str = Query(default="")):
    with get_db() as db:
        if status:
            rows = db.execute("SELECT * FROM clients WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/clients")
def create_client(data: dict):
    cid = str(uuid.uuid4())[:8]
    with get_db() as db:
        db.execute("""
            INSERT INTO clients (id, hotel_name, contact_name, contact_phone, source, area, rooms,
                hotel_level, hotel_type, address, notes, status, created_by, created_by_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cid,
            data.get("hotelName", ""),
            data.get("contactName", ""),
            data.get("contactPhone", ""),
            data.get("source", ""),
            float(data.get("area", 0)),
            int(data.get("rooms", 0)),
            data.get("hotelLevel", "经济型"),
            data.get("hotelType", "连锁型"),
            data.get("address", ""),
            data.get("notes", ""),
            "待处理",
            data.get("userId", ""),
            data.get("userName", "匿名"),
        ))
    with get_db() as db:
        row = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else {}


@app.put("/api/clients/{cid}")
def update_client(cid: str, data: dict):
    with get_db() as db:
        existing = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if not existing:
            raise HTTPException(404, "客资不存在")
        db.execute("""
            UPDATE clients SET hotel_name=?, contact_name=?, contact_phone=?, source=?,
                area=?, rooms=?, hotel_level=?, hotel_type=?, address=?, notes=?,
                status=?, updated_at=?
            WHERE id=?
        """, (
            data.get("hotelName", existing["hotel_name"]),
            data.get("contactName", existing["contact_name"]),
            data.get("contactPhone", existing["contact_phone"]),
            data.get("source", existing["source"]),
            float(data.get("area", existing["area"])),
            int(data.get("rooms", existing["rooms"])),
            data.get("hotelLevel", existing["hotel_level"]),
            data.get("hotelType", existing["hotel_type"]),
            data.get("address", existing["address"]),
            data.get("notes", existing["notes"]),
            data.get("status", existing["status"]),
            now_str(),
            cid,
        ))
    with get_db() as db:
        row = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else {}


@app.delete("/api/clients/{cid}")
def delete_client(cid: str):
    with get_db() as db:
        db.execute("DELETE FROM clients WHERE id=?", (cid,))
    return {"ok": True}


@app.post("/api/clients/{cid}/claim")
def claim_client(cid: str, data: dict):
    uid = data.get("userId", "")
    uname = data.get("userName", "匿名")
    with get_db() as db:
        existing = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if not existing:
            raise HTTPException(404, "客资不存在")
        if existing["claimed_by"] and existing["claimed_by"] != uid:
            raise HTTPException(409, f"该客资已被 {existing['claimed_by_name']} 认领")
        db.execute("""
            UPDATE clients SET claimed_by=?, claimed_by_name=?, status='已认领', updated_at=?
            WHERE id=?
        """, (uid, uname, now_str(), cid))
    with get_db() as db:
        row = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else {}


# 测算
@app.post("/api/calculate")
def run_calculate(data: dict):
    params = data.get("params", {})
    result = calc_engine(params)
    if result.get("error"):
        raise HTTPException(400, result["error"])

    cid = data.get("clientId", "")
    uid = data.get("userId", "")
    uname = data.get("userName", "")

    calc_id = str(uuid.uuid4())[:8]
    with get_db() as db:
        # 如果 clientId 为空则用空串（避免 NOT NULL 约束），由外键参照自身处理
        db_client_id = cid if cid and len(cid) == 8 else ''
        # 先确保空串不触发外键：关闭外键检查插入后再开启
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("""
            INSERT INTO calculations (id, client_id, params_json, results_json, created_by, created_by_name)
            VALUES (?,?,?,?,?,?)
        """, (calc_id, db_client_id, json.dumps(params, ensure_ascii=False), json.dumps(result, ensure_ascii=False), uid, uname))
        db.execute("PRAGMA foreign_keys=ON")

        if cid and len(cid) == 8:
            db.execute("UPDATE clients SET status='已测算', updated_at=? WHERE id=?", (now_str(), cid))

    result["calcId"] = calc_id
    return result


@app.get("/api/calculations")
def list_calculations(client_id: str = Query(default="")):
    with get_db() as db:
        if client_id:
            rows = db.execute("SELECT * FROM calculations WHERE client_id=? ORDER BY created_at DESC", (client_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM calculations ORDER BY created_at DESC").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params_json"])
        d["results"] = json.loads(d["results_json"])
        results.append(d)
    return results


# 导出 PDF（生成 HTML 后用浏览器打印）
@app.get("/api/export-report/{calc_id}")
def export_report(calc_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM calculations WHERE id=?", (calc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "测算记录不存在")
    row = dict(row)
    r = json.loads(row["results_json"])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>致城酒店投资测算报告</title>
<style>
  @page {{ size: A4; margin: 12mm; }}
  body {{ font-family: 'PingFang SC','Microsoft YaHei',sans-serif; font-size:12px; color:#1a1a1c; line-height:1.7; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:15px; border-bottom:2px solid #b8963e; padding-bottom:4px; margin:20px 0 10px; color:#1e3a5f; }}
  h3 {{ font-size:13px; margin:14px 0 6px; color:#1e3a5f; }}
  .header {{ display:flex; justify-content:space-between; align-items:flex-start; border-bottom:3px solid #b8963e; padding-bottom:12px; margin-bottom:16px; }}
  .header .title h1 {{ margin:0; }}
  .header .meta {{ font-size:10px; color:#888; text-align:right; }}
  .verdict {{ display:inline-block; padding:4px 16px; border-radius:4px; font-size:16px; font-weight:700; color:white; background:{r.get('verdictColor','#888')}; }}
  .kpi-grid {{ display:flex; flex-wrap:wrap; gap:2px; background:#e0e0e0; margin:10px 0; }}
  .kpi {{ flex:1; min-width:140px; background:white; padding:10px 12px; }}
  .kpi .label {{ font-size:9px; color:#888; text-transform:uppercase; }}
  .kpi .value {{ font-size:16px; font-weight:700; font-family:'SF Mono','Consolas',monospace; }}
  table {{ width:100%; border-collapse:collapse; margin:8px 0; font-size:11px; }}
  th {{ background:#f5f3f0; padding:6px 8px; text-align:left; font-size:9px; text-transform:uppercase; color:#666; border-bottom:2px solid #ccc; }}
  td {{ padding:5px 8px; border-bottom:1px solid #eee; }}
  td.num {{ text-align:right; font-family:'SF Mono','Consolas',monospace; }}
  .dual {{ display:flex; gap:12px; }}
  .dual>div {{ flex:1; padding:8px; border-radius:6px; }}
  .dual .stable {{ background:#f0f8f5; }}
  .dual .cons {{ background:#fef5f4; }}
  .footer {{ margin-top:24px; font-size:9px; color:#aaa; text-align:center; border-top:1px solid #ddd; padding-top:8px; }}
</style></head>
<body>
<div class="header">
  <div class="title">
    <h1>致城酒店投资测算报告</h1>
    <div style="font-size:12px;color:#555;">{r.get('hotelName','')}</div>
  </div>
  <div class="meta">
    打印时间：{now_str()}<br>
    测算人：{row.get('created_by_name','')}
  </div>
</div>

<div style="text-align:center;margin:16px 0;">
  <span class="verdict">{r.get('verdict','')}</span>
</div>

<h2>投资决策概览</h2>
<div class="kpi-grid">
  <div class="kpi"><div class="label">项目介入预算</div><div class="value">¥{r['investBudget']:,.0f}</div></div>
  <div class="kpi"><div class="label">年化回报(稳健)</div><div class="value">{r['roiYears']['stable']} 年</div></div>
  <div class="kpi"><div class="label">年化回报(保守)</div><div class="value">{r['roiYears']['conservative']} 年</div></div>
  <div class="kpi"><div class="label">转让预算</div><div class="value">¥{r['transferBudget']:,.0f}</div></div>
  <div class="kpi"><div class="label">房押预算</div><div class="value">¥{r['depositBudget']:,.0f}</div></div>
</div>

<h2>项目测算明细</h2>

<h3>租金测算</h3>
<table>
  <tr><th>项目</th><th>数值</th><th>项目</th><th>数值</th></tr>
  <tr><td>年固定租金</td><td class="num">¥{r['yearFixedRent']:,.2f}</td><td>年浮动租金</td><td class="num">¥{r['yearFloatRent']:,.2f}</td></tr>
  <tr><td>年总租金</td><td class="num">¥{r['yearTotalRent']:,.2f}</td><td>月租金</td><td class="num">¥{r['monthRent']:,.2f}</td></tr>
  <tr><td>日租金</td><td class="num">¥{r['dayRent']:,.2f}</td><td>单房日租金</td><td class="num">¥{r['roomDayRent']:,.2f}</td></tr>
</table>

<h3>收入测算</h3>
<table>
  <tr><th></th><th>旺季</th><th>平季</th><th>淡季</th><th>全年</th></tr>
  <tr><td>季度收入</td><td class="num">¥{r['peakRevenue']:,.0f}</td><td class="num">¥{r['midRevenue']:,.0f}</td><td class="num">¥{r['lowRevenue']:,.0f}</td><td class="num">¥{r['yearRevenue']['stable']:,.0f}</td></tr>
  <tr><td>RevPar</td><td class="num">—</td><td class="num">—</td><td class="num">—</td><td class="num">¥{r['yearRevPar']['stable']:,.2f}</td></tr>
</table>

<div class="dual">
  <div class="stable">
    <strong>稳健测算</strong>
    <table>
      <tr><td>全年收入</td><td class="num">¥{r['yearRevenue']['stable']:,.2f}</td></tr>
      <tr><td>RevPar</td><td class="num">¥{r['yearRevPar']['stable']:,.2f}</td></tr>
      <tr><td>ADR</td><td class="num">¥{r['yearADR']['stable']:,.2f}</td></tr>
      <tr><td>OCC</td><td class="num">{r['yearOCC']:.1f}%</td></tr>
      <tr><td>间夜数</td><td class="num">{r['yearRoomNights']['stable']:,.0f}</td></tr>
      <tr><td>GOP毛利</td><td class="num">¥{r['gopGross']['stable']:,.2f}</td></tr>
      <tr><td>GOP率</td><td class="num">{r['gopRate']['stable']:.1f}%</td></tr>
      <tr><td>经营方月纯利</td><td class="num">¥{r['monthNetProfit']['stable']:,.2f}</td></tr>
      <tr><td>年化回报</td><td class="num">{r['roiYears']['stable']} 年</td></tr>
    </table>
  </div>
  <div class="cons">
    <strong>保守测算</strong>
    <table>
      <tr><td>全年收入</td><td class="num">¥{r['yearRevenue']['conservative']:,.2f}</td></tr>
      <tr><td>RevPar</td><td class="num">¥{r['yearRevPar']['conservative']:,.2f}</td></tr>
      <tr><td>ADR</td><td class="num">¥{r['yearADR']['conservative']:,.2f}</td></tr>
      <tr><td>OCC</td><td class="num">—</td></tr>
      <tr><td>间夜数</td><td class="num">{r['yearRoomNights']['conservative']:,.0f}</td></tr>
      <tr><td>GOP毛利</td><td class="num">¥{r['gopGross']['conservative']:,.2f}</td></tr>
      <tr><td>GOP率</td><td class="num">{r['gopRate']['conservative']:.1f}%</td></tr>
      <tr><td>经营方月纯利</td><td class="num">¥{r['monthNetProfit']['conservative']:,.2f}</td></tr>
      <tr><td>年化回报</td><td class="num">{r['roiYears']['conservative']} 年</td></tr>
    </table>
  </div>
</div>

<h3>成本与盈亏平衡</h3>
<table>
  <tr><th>项目</th><th>稳健</th><th>保守</th></tr>
  <tr><td>全年总成本</td><td class="num">¥{r['yearTotalCost']['stable']:,.2f}</td><td class="num">¥{r['yearTotalCost']['conservative']:,.2f}</td></tr>
  <tr><td>间夜总成本</td><td class="num">¥{r['nightTotalCost']:,.2f}</td><td class="num">—</td></tr>
  <tr><td>盈亏平衡RevPar</td><td class="num">¥{r['beRevPar']['stable']:,.2f}</td><td class="num">¥{r['beRevPar']['conservative']:,.2f}</td></tr>
  <tr><td>盈亏平衡出租率</td><td class="num">{r['beOCC']['stable']:.1f}%</td><td class="num">{r['beOCC']['conservative']:.1f}%</td></tr>
</table>

<div class="footer">致城酒店投资测算系统 · 本报告仅供内部决策参考</div>
</body></html>"""
    return HTMLResponse(content=html)


# 首页
@app.get("/", response_class=HTMLResponse)
def index():
    html_path = BASE_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>请将 index.html 放在与本程序同目录下</h1>"


# ─── 启动 ────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    ip = get_local_ip()
    print(f"""
╔══════════════════════════════════════════════════╗
║       致城酒店投资测算系统 v1.0                    ║
║                                                  ║
║   本机访问: http://localhost:{PORT}                ║
║   局域网访问: http://{ip}:{PORT}             ║
║                                                  ║
║   其他人可通过局域网地址访问，实现多人协同。        ║
║   按 Ctrl+C 停止服务。                            ║
╚══════════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
