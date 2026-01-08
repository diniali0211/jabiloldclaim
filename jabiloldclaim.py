import io
from pathlib import Path
from datetime import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st


# ───────────────────────────── UI header ─────────────────────────────
st.set_page_config(page_title="Agency Claim Table Generator (Auto Recruiter)", layout="wide")
st.title("🧾 Agency Claim Table Generator (Auto-detect Recruiter)")

st.markdown(
    """
- **Masterlist**: must include **Name**, **Joined Date**, and **Recruiter** (optional but recommended).  
- **Timecard**: must include **Emp No**, **Name**, **Date**, and **one IN + one OUT** column.  
- A workday counts **1** if **daily hours ≥ (hours per day − grace)** and **not on leave**.
- Eligibility window: **JOIN_DATE → JOIN_DATE + 3 months − 1 day**
- Claim cycle: **24th → 23rd of the next month**
"""
)

# ───────────────────────────── Sidebar ─────────────────────────────
with st.sidebar:
    st.header("Settings")
    hours_per_day = st.number_input("Hours considered 1 workday", 1.0, 24.0, 8.0, 0.5)
    grace_minutes = st.number_input("Grace window (minutes)", 0, 120, 15, 5)
    day_rate = st.number_input("Rate per claim day (RM)", 0.0, 1000.0, 3.0, 0.5)
    day_first = st.checkbox("Dates are day-first (DD/MM/YYYY)", value=True)

effective_threshold = max(0.0, float(hours_per_day) - float(grace_minutes) / 60.0)

# ───────────────────────────── Uploaders ─────────────────────────────
att_file = st.file_uploader("Upload **Timecard**", type=["csv", "xlsx", "xls"])
mst_file = st.file_uploader("Upload **Masterlist**", type=["xlsx", "xls"])


# ───────────────────────────── Helpers ─────────────────────────────
def ensure_unique_headers(df):
    counts, cols = {}, []
    for c in df.columns:
        c = str(c).strip()
        counts[c] = counts.get(c, 0) + 1
        cols.append(c if counts[c] == 1 else f"{c}_{counts[c]}")
    df.columns = cols
    return df


def _norm_empid(v):
    if pd.isna(v):
        return np.nan
    v = str(v).strip().upper().replace(" ", "")
    return v[:-2] if v.endswith(".0") else v


def _norm_name(v):
    return "" if pd.isna(v) else str(v).strip()


def _is_leave(v):
    if v is None:
        return False
    v = str(v).lower()
    return any(k in v for k in ["leave", "mc", "medical", "sick", "absent", "unpaid"])


def _pair_duration(i, o):
    try:
        hi = pd.to_datetime(i).hour + pd.to_datetime(i).minute / 60
        ho = pd.to_datetime(o).hour + pd.to_datetime(o).minute / 60
        return ho - hi if ho >= hi else ho + 24 - hi
    except Exception:
        return 0.0


def _parse_dates(s, day_first):
    d = pd.to_datetime(s, errors="coerce", dayfirst=day_first)
    mask = d.isna() & s.astype(str).str.fullmatch(r"\d+(\.0)?")
    d.loc[mask] = pd.to_datetime(
        s[mask].astype(float), unit="d", origin="1899-12-30"
    )
    return d


# 🔹 CLAIM CYCLE HELPER (24 → 26)
def claim_cycle_info(d):
    if d.day >= 24:
        start = d.replace(day=24)
    else:
        start = (d - pd.DateOffset(months=1)).replace(day=24)
    end = (start + pd.DateOffset(months=1)).replace(day=23)
    return f"{start.date()}_to_{end.date()}", start, end


# ───────────────────────────── Main ─────────────────────────────
if att_file and mst_file:
    try:
        # ----- Load files -----
        att = pd.read_csv(att_file, dtype=str) if Path(att_file.name).suffix == ".csv" else pd.read_excel(att_file, dtype=str)
        mst = pd.read_excel(mst_file, dtype=str)

        att = ensure_unique_headers(att)
        mst = ensure_unique_headers(mst)

        # ----- Mapping -----
        t_date = st.selectbox("Timecard — Date", att.columns)
        t_name = st.selectbox("Timecard — Name", att.columns)
        t_emp  = st.selectbox("Timecard — Emp No", att.columns)
        t_in   = st.selectbox("Timecard — IN", att.columns)
        t_out  = st.selectbox("Timecard — OUT", att.columns)
        t_leave = st.selectbox("Timecard — Leave", ["(none)"] + list(att.columns))

        m_name = st.selectbox("Masterlist — Name", mst.columns)
        m_emp  = st.selectbox("Masterlist — Emp No", mst.columns)
        m_join = st.selectbox("Masterlist — Joined Date", mst.columns)
        m_rec  = st.selectbox("Masterlist — Recruiter", mst.columns)

        # ----- Normalize -----
        att["__Date"] = _parse_dates(att[t_date], day_first)
        att = att[att["__Date"].notna()]
        att["__EmpID"] = att[t_emp].apply(_norm_empid)
        att["__Name"] = att[t_name].apply(_norm_name)
        att["__Leave"] = att[t_leave].apply(_is_leave) if t_leave != "(none)" else False
        att["__Hours"] = [_pair_duration(r[t_in], r[t_out]) for _, r in att.iterrows()]

        mst[m_join] = pd.to_datetime(mst[m_join], errors="coerce")
        mst[m_emp] = mst[m_emp].apply(_norm_empid)

        join_map = dict(zip(mst[m_emp], mst[m_join]))
        rec_map = dict(zip(mst[m_emp], mst[m_rec]))

        att["JOIN_DATE"] = att["__EmpID"].map(join_map)
        att["Recruiter"] = att["__EmpID"].map(rec_map)
        att = att[att["JOIN_DATE"].notna()]

        # ----- Eligibility (NO 30 DAYS) -----
        att["ELIGIBLE_END"] = att["JOIN_DATE"] + pd.DateOffset(months=3) - pd.Timedelta(days=1)

        eligible = att[
            (att["__Date"] >= att["JOIN_DATE"]) &
            (att["__Date"] <= att["ELIGIBLE_END"])
        ].copy()

        # ----- Assign claim cycles -----
        cycle_info = eligible["__Date"].apply(claim_cycle_info)
        eligible["CLAIM_CYCLE"] = cycle_info.apply(lambda x: x[0])
        eligible["CYCLE_START"] = cycle_info.apply(lambda x: x[1])
        eligible["CYCLE_END"]   = cycle_info.apply(lambda x: x[2])

        # ----- Daily aggregation -----
        daily = (
            eligible.groupby(
                ["__EmpID","__Name","JOIN_DATE","ELIGIBLE_END",
                 "Recruiter","__Date","CLAIM_CYCLE","CYCLE_START","CYCLE_END"],
                as_index=False
            )
            .agg(
                Total_Hours=("__Hours","sum"),
                Any_Leave=("__Leave","max")
            )
        )

        daily["Worked_Day"] = np.where(
            daily["Any_Leave"], 0,
            (daily["Total_Hours"] >= effective_threshold).astype(int)
        )

        # ----- Build cycle tables -----
        tables, summaries = {}, {}

        for cyc in daily["CLAIM_CYCLE"].unique():
            dcy = daily[daily["CLAIM_CYCLE"] == cyc]
            days = pd.date_range(dcy["CYCLE_START"].iloc[0], dcy["CYCLE_END"].iloc[0])

            labels = [d.strftime("%d-%b") for d in days]

            base = (
                dcy.groupby(
                    ["__EmpID","__Name","JOIN_DATE","ELIGIBLE_END","Recruiter"],
                    as_index=False
                )
                .agg(
                    Total_Hours=("Total_Hours","sum"),
                    TOTAL_WORKING=("Worked_Day","sum")
                )
            )

            for lab in labels:
                base[lab] = 0

            for _, r in dcy.iterrows():
                lab = r["__Date"].strftime("%d-%b")
                base.loc[
                    (base["__EmpID"] == r["__EmpID"]) &
                    (base["__Name"] == r["__Name"]),
                    lab
                ] = r["Worked_Day"]

            base = base.rename(columns={"__EmpID":"Emp No","__Name":"Name"})

            ordered_cols = (
                ["Emp No","Name","JOIN_DATE","ELIGIBLE_END","Recruiter"]
                + labels
                + ["TOTAL_WORKING","Total_Hours"]
            )

            tables[cyc] = base[ordered_cols]

            s = base.groupby("Recruiter", as_index=False)["TOTAL_WORKING"].sum()
            s["Rate (RM)"] = day_rate
            s["Amount (RM)"] = s["TOTAL_WORKING"] * day_rate
            summaries[cyc] = s

        # ----- Display + Export -----
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            for cyc in tables:
                sheet = str(cyc)[:31]
                tables[cyc].to_excel(writer, sheet_name=sheet, index=False)
                summaries[cyc].to_excel(
                    writer,
                    sheet_name=sheet,
                    startrow=len(tables[cyc]) + 2,
                    index=False
                )

        for cyc, df in tables.items():
            st.subheader(f"📅 Claim Cycle {cyc}")
            st.dataframe(df, use_container_width=True)
            st.dataframe(summaries[cyc], use_container_width=True)

        st.download_button(
            "⬇️ Download Excel (Claim Cycle Tables + Summary)",
            buffer.getvalue(),
            f"claim_cycles_{dt.now().strftime('%Y%m%d_%H%M')}.xlsx"
        )

    except Exception as e:
        st.error(str(e))
        st.exception(e)
else:
    st.info("Upload both files to continue.")



