
import streamlit as st, pandas as pd, re, random, datetime, tempfile, os
from collections import defaultdict
from utils.generate_calendar import generate_pretty_calendar

SPACE=3  # min days between any duty/oncall

def shift_sort_key(col):
    day=int(re.match(r'(\d+)',col).group(1))
    sub=int(re.search(r'-(\d)',col).group(1)) if '-' in col else 0
    return (day,sub)

def build_schedule(df_raw:pd.DataFrame, year:int, month:int):
    doctors=df_raw['Name'].tolist()
    group=df_raw.set_index('Name')['Group'].to_dict()
    avail=1-df_raw.drop(columns=['Group','Name'])
    shifts=sorted(avail.columns,key=shift_sort_key)
    day_of={c:int(re.match(r'(\d+)',c).group(1)) for c in shifts}
    sub_of={c:int(re.search(r'-(\d)',c).group(1)) if '-' in c else 0 for c in shifts}
    kind={c:'holiday_double' if sub_of[c]==1 else 'single' for c in shifts}
    duty=defaultdict(dict); oncall={}
    last={d:-10 for d in doctors}
    duty_cnt=defaultdict(int); oncall_cnt=defaultdict(int)
    weekly=defaultdict(lambda:defaultdict(int)); hol1=defaultdict(int)
    def can(d,col):
        return (avail.loc[df_raw['Name']==d,col].iat[0]==1 and
                day_of[col]-last[d]>=SPACE and
                weekly[d][datetime.date(year,month,day_of[col]).isocalendar().week]==0 and
                (hol1[d]<1 if sub_of[col]==1 else True))
    g1=[d for d in doctors if group[d]==1]
    g0=[d for d in doctors if group[d]==0]
    hd=[c for c in shifts if sub_of[c]==1]; sg=[c for c in shifts if sub_of[c]!=1]
    random.shuffle(g1)
    for doc in g1:
        for colset in (hd,sg):
            for col in colset:
                if 'G1' in duty[col].values(): continue
                if can(doc,col):
                    duty[col][doc]='G1'; duty_cnt[doc]+=1; last[doc]=day_of[col]
                    weekly[doc][datetime.date(year,month,day_of[col]).isocalendar().week]=1
                    if sub_of[col]==1: hol1[doc]+=1
                    if duty_cnt[doc]==2: break
            if duty_cnt[doc]==2: break
    random.shuffle(g0)
    for doc in g0:
        if duty_cnt[doc]>0: continue
        for col in shifts:
            need=(sub_of[col]==1 and 'G0' not in duty[col].values()) or (sub_of[col]!=1 and not duty[col])
            if need and can(doc,col):
                duty[col][doc]='G0'; duty_cnt[doc]+=1; last[doc]=day_of[col]
                weekly[doc][datetime.date(year,month,day_of[col]).isocalendar().week]=1
                if sub_of[col]==1: hol1[doc]+=1
                break
    for col in shifts:
        if (sub_of[col]==1 and 'G0' not in duty[col].values()) or (sub_of[col]!=1 and not duty[col]):
            c=[d for d in g0 if duty_cnt[d]<2 and can(d,col)]
            if c:
                doc=random.choice(c)
                duty[col][doc]='G0'; duty_cnt[doc]+=1; last[doc]=day_of[col]
                weekly[doc][datetime.date(year,month,day_of[col]).isocalendar().week]=1
                if sub_of[col]==1: hol1[doc]+=1
    for col in sg:
        if 'G1' in duty[col].values():
            c=[d for d in g0 if d not in duty[col] and can(d,col)]
            if not c:
                c=[d for d in g0 if d not in duty[col] and avail.loc[df_raw['Name']==d,col].iat[0]==1]
            if c:
                doc=random.choice(c)
                oncall[col]=doc; oncall_cnt[doc]+=1; last[doc]=day_of[col]
    rows=[]
    for col in shifts:
        rows.append({'Shift':col,
                     'Duty_G0':', '.join([d for d,g in duty[col].items() if g=='G0']),
                     'Duty_G1':', '.join([d for d,g in duty[col].items() if g=='G1']),
                     'Oncall_G0':oncall.get(col,'')})
    sched=pd.DataFrame(rows)
    summary=pd.DataFrame({'Group':[group[d] for d in doctors],
                          'Duty':[duty_cnt[d] for d in doctors],
                          'Oncall':[oncall_cnt[d] for d in doctors]}, index=doctors)
    summary['Total']=summary['Duty']+summary['Oncall']
    return sched, summary

st.title("Doctor Duty Scheduler")
uploaded=st.file_uploader("当直希望 CSV をアップロード (0=可/1=NG)", type=["csv"])
if uploaded:
    df=pd.read_csv(uploaded, encoding='cp932')
    sched, summary = build_schedule(df, 2025, 6)
    st.subheader("Schedule")
    st.dataframe(sched, hide_index=True, use_container_width=True)
    st.subheader("Summary")
    st.dataframe(summary, use_container_width=True)
    # Downloads
    with tempfile.TemporaryDirectory() as tmp:
        excel_path=os.path.join(tmp,"schedule.xlsx")
        with pd.ExcelWriter(excel_path) as w:
            sched.to_excel(w, sheet_name="Schedule", index=False)
            summary.to_excel(w, sheet_name="Summary")
        with open(excel_path,"rb") as f:
            st.download_button("Excel をダウンロード", f, file_name="schedule.xlsx")
        cal_path=os.path.join(tmp,"pretty_calendar.xlsx")
        generate_pretty_calendar(sched, 2025, 6, set(), cal_path)
        with open(cal_path,"rb") as f:
            st.download_button("カレンダーをダウンロード", f, file_name="pretty_calendar.xlsx")
else:
    st.info("CSV をアップロードしてください。")
