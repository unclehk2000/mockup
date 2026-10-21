#!/usr/bin/env python3
"""
mockup.py - TradingView-style chart mock-ups of any futures day from 1-minute CSVs, for visual back-testing.
Render one morning, or every trading day in a range, then flip through the PNGs and mark what happened.

  python mockup.py --data "nq/*.csv" --date 2026-06-17
  python mockup.py --data "nq/*.csv" --date 2026-06-01..2026-06-30 --out june/        one PNG per trading day
  python mockup.py --data "nq/*.csv" --date 2026-06-17 --start 08:00 --end 10:15 --tf 5 --theme dark
  python mockup.py --data "nq/*.csv" --date 2026-06-17 --level 30400:Target --level 30250:Stop:#f23645
  python mockup.py --data "nq/*.csv" --date last --layout mobile

No look-ahead: everything on the chart and the card is built from bars up to --end, so moving --end replays
the day minute by minute exactly as it looked at that time.

Data (any of these, mixed files fine):
  Barchart continuous export   Date,Time,Open,High,Low,Close,...,Volume,Contract   (US Central: shifted +1 h to ET)
  Barchart per-contract file   Time,Open,High,Low,Latest,...,Volume  (contract from the file name, e.g. nqz26_...)
  Generic CSV                  a date/time (or unix-seconds 'time') column plus open, high, low, close[, volume]
Times are ET. A trading day runs 18:00 (the evening before) to 17:00. When several contracts trade the same day,
the one with the most volume is used.

On the chart (--show, comma list; default all):  pdhl  prior day's high / low
  overnight  18:00-09:29 high / low and midpoint     asia  20:00-00:00 box     london  02:00-05:00 box
  or         09:30-09:59 opening range              open  09:30 line
Card (--card): auto (default), none, or a JSON file: {"title": "...", "pill": "...", "color": "#2962ff",
  "rows": [["KEY", "value", "right"], ...]}
Requires numpy, pandas, matplotlib.
"""
import argparse, glob, json, os, re, sys
from datetime import time as T, timedelta
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

THEMES = {
    'light': dict(bg='#ffffff', fg='#131722', grid='#eef1f6', up='#089981', dn='#f23645', body='#f4f6f9', sub='#5d6476', frame='#d1d4dc'),
    'dark': dict(bg='#131722', fg='#d1d4dc', grid='#1e222d', up='#089981', dn='#f23645', body='#161a23', sub='#8a93a6', frame='#2a2e39'),
}
C_PD, C_ON, C_ASIA, C_LDN, C_OR, C_LVL = '#787b86', '#9c27b0', '#f7a600', '#2962ff', '#26a69a', '#ff9800'


# ─────────────────────────────────────────────────────────── data
def _col(cols, *names):
    return next((cols[n] for n in names if n in cols), None)


def load(patterns, tz_shift=None):
    files = sorted({f for p in patterns for f in glob.glob(p)})
    if not files:
        sys.exit(f'no files match {patterns}')
    frames = []
    for f in files:
        try:
            d = pd.read_csv(f, dtype=str)
        except Exception as e:
            print(f'  skip {os.path.basename(f)}: {e}'); continue
        cols = {c.lower().strip(): c for c in d.columns}
        o, h, l = _col(cols, 'open'), _col(cols, 'high'), _col(cols, 'low')
        c = _col(cols, 'close', 'last', 'latest')
        if not (o and h and l and c):
            print(f'  skip {os.path.basename(f)}: no open / high / low / close columns'); continue
        barchart = 'latest' in cols or 'contract' in cols
        dcol, tcol = _col(cols, 'date'), _col(cols, 'time')
        if dcol and tcol and not d[tcol].astype(str).str.contains(r'\d{4}-\d\d-\d\d', na=False).any():
            stamp = d[dcol].astype(str) + ' ' + d[tcol].astype(str)
        else:
            stamp = d[_col(cols, 'datetime', 'timestamp', 'time', 'date')].astype(str)
        if stamp.str.fullmatch(r'\d{9,10}', na=False).mean() > 0.9:            # unix seconds (TradingView export)
            dt = pd.to_datetime(pd.to_numeric(stamp, errors='coerce'), unit='s', utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
            shift = 0.0
        else:
            dt = pd.to_datetime(stamp, errors='coerce')
            shift = (1.0 if barchart else 0.0) if tz_shift is None else tz_shift
        k = _col(cols, 'contract', 'symbol')
        if k:
            kk = d[k].astype(str)
        else:
            m = re.match(r'([a-z]{1,3})([fghjkmnquvxz])(\d\d)', os.path.basename(f), re.I)
            kk = pd.Series(f'{m.group(1)}{m.group(2)}{m.group(3)}'.upper() if m else os.path.splitext(os.path.basename(f))[0], index=d.index)
        v = _col(cols, 'volume', 'vol')
        x = pd.DataFrame({'dt': dt + pd.Timedelta(hours=shift), 'o': d[o], 'h': d[h], 'l': d[l], 'c': d[c],
                          'v': d[v] if v else '0', 'k': kk})
        frames.append(x)
    if not frames:
        sys.exit('no usable files')
    df = pd.concat(frames, ignore_index=True)
    for col in 'ohlcv':
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df = df.dropna(subset=['dt', 'o', 'h', 'l', 'c']).drop_duplicates(['dt', 'k']).sort_values('dt').reset_index(drop=True)
    df['v'] = df.v.fillna(0)
    n = df.dt.dt.normalize()
    df['td'] = n.where(df.dt.dt.hour < 18, n + pd.Timedelta(days=1))
    # one contract per trading day: the most traded (or most bars when there is no volume)
    w = df.groupby(['td', 'k']).agg(v=('v', 'sum'), n=('dt', 'size')).reset_index()
    w['score'] = w.v + w.n * 1e-6
    front = w.sort_values('score').groupby('td').tail(1).set_index('td').k
    df = df[df.k.values == front.reindex(df.td).values].reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────── levels (from bars up to `asof` only)
def hl(x):
    return (float(x.h.max()), float(x.l.min())) if len(x) else (np.nan, np.nan)


def first_touch(x, price, above):
    hit = x[x.h >= price] if above else x[x.l <= price]
    return hit.dt.iloc[0] if len(hit) else None


def levels(df, td, asof):
    days = sorted(df.td.unique())
    i = days.index(td)
    D = df[(df.td == td) & (df.dt <= asof)]
    out = {'D': D, 'last': float(D.c.iloc[-1]), 'k': D.k.iloc[0]}
    if i > 0:
        P = df[df.td == days[i - 1]]
        out['pdh'], out['pdl'] = hl(P)
        out['prev_close'] = float(P.c.iloc[-1])
        rth = P[(P.dt.dt.time >= T(9, 30)) & (P.dt.dt.time <= T(16, 14))]
        out['rth_close'] = float(rth.c.iloc[-1]) if len(rth) else np.nan
        out['same_contract'] = P.k.iloc[0] == out['k']
    rng = [df[df.td == d].pipe(lambda x: x.h.max() - x.l.min()) for d in days[max(0, i - 20):i]]
    out['adr'] = float(np.mean(rng)) if len(rng) == 20 else np.nan
    t = D.dt.dt.time
    on = D[(t >= T(18, 0)) | (t < T(9, 30))]
    out['on'] = hl(on)
    asia = D[(t >= T(20, 0))]
    ldn = D[(t >= T(2, 0)) & (t < T(5, 0))]
    orr = D[(t >= T(9, 30)) & (t < T(10, 0))]
    out['asia'], out['ldn'], out['or'] = hl(asia), hl(ldn), hl(orr)
    out['asia_done'], out['ldn_done'], out['or_done'] = asof.time() >= T(0, 0) and asof.hour < 18, asof.time() >= T(5, 0) and asof.hour < 18, asof.time() >= T(10, 0) and asof.hour < 18
    op = D[t == T(9, 30)]
    out['open'] = float(op.o.iloc[0]) if len(op) else np.nan
    return out


# ─────────────────────────────────────────────────────────── card
def fmt(v, nd=2):
    return '—' if v is None or (isinstance(v, float) and np.isnan(v)) else f'{v:,.{nd}f}'


def auto_card(L, asof, nd):
    D = L['D']; last = L['last']; rows = []
    rng = float(D.h.max() - D.l.min())
    rows.append(('RANGE', f'{rng:,.{0 if nd <= 2 else nd}f} pts' + (f' · {rng / L["adr"] * 100:.0f}% of ADR20' if not np.isnan(L['adr']) else ''),
                 '' if np.isnan(L['adr']) else f'ADR {L["adr"]:,.0f}', None))
    onh, onl = L['on']
    if not np.isnan(onh):
        mid = (onh + onl) / 2
        rows.append(('O/N', f'{fmt(onh, nd)} / {fmt(onl, nd)} · mid {fmt(mid, nd)}', 'above mid' if last > mid else 'below mid', None))
    if 'pdh' in L:
        for key, px, above in (('PDH', L['pdh'], True), ('PDL', L['pdl'], False)):
            t = first_touch(D, px, above)
            rows.append((key, fmt(px, nd), f'taken {t:%H:%M}' if t is not None else f'{abs(px - last):,.{min(nd, 2)}f} away', C_PD if t is not None else None))
    td = D.td.iloc[0]
    for key, (h, l), done, endt in (('ASIA', L['asia'], L['asia_done'], td), ('LONDON', L['ldn'], L['ldn_done'], td + pd.Timedelta(hours=5))):
        if np.isnan(h) or not done:
            continue
        rest = D[D.dt >= endt]
        th, tl = first_touch(rest, h, True), first_touch(rest, l, False)
        tag = ' · '.join(s for s in ((f'H {th:%H:%M}' if th is not None else ''), (f'L {tl:%H:%M}' if tl is not None else '')) if s) or 'untouched'
        rows.append((key, f'{fmt(h, nd)} / {fmt(l, nd)}', tag, None))
    if not np.isnan(L['open']) and not np.isnan(L.get('rth_close', np.nan)):
        g = L['open'] - L['rth_close']
        rth = D[D.dt >= td + pd.Timedelta(hours=9, minutes=30)]
        fill = first_touch(rth, L['rth_close'], g < 0)
        rows.append(('GAP', f'{g:+,.{min(nd, 2)}f} pts (16:14 → 09:30)', f'filled {fill:%H:%M}' if fill is not None else 'open', None))
    if L['or_done'] and not np.isnan(L['or'][0]):
        oh, ol = L['or']; rest = D[D.dt >= td + pd.Timedelta(hours=10)]
        bu, bd = first_touch(rest, oh + 1e-9, True), first_touch(rest, ol - 1e-9, False)
        first = 'none yet' if bu is None and bd is None else ('up ' + f'{bu:%H:%M}' if bd is None or (bu is not None and bu <= bd) else 'down ' + f'{bd:%H:%M}')
        rows.append(('OR', f'{fmt(oh, nd)} / {fmt(ol, nd)}', f'break {first}', None))
    pc = L.get('prev_close')
    chg = (last / pc - 1) * 100 if pc else np.nan
    pill = '—' if np.isnan(chg) else f'{chg:+.2f}%'
    col = '#787b86' if np.isnan(chg) else ('#089981' if chg >= 0 else '#f23645')
    return dict(title=f'{L["k"]} · {fmt(last, nd)}', sub=f'{asof:%m/%d · %H:%M} ET', pill=pill, color=col, rows=rows)


def lum(c):
    c = c.lstrip('#'); r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4)); return 0.299 * r + 0.587 * g + 0.114 * b


def draw_card(fig, ax, card, th, fs, mono=True):
    fam = 'DejaVu Sans Mono' if mono else 'DejaVu Sans'
    rows = [r if len(r) == 4 else (r[0], r[1], r[2] if len(r) > 2 else '', None) for r in card['rows']]
    hc = card.get('color', '#2962ff')
    W, H = fig.get_size_inches(); rd = fig.canvas.get_renderer()
    def tw(s):
        t = fig.text(0, 0, s, fontsize=fs, family=fam); w = t.get_window_extent(rd).width / fig.dpi / W; t.remove(); return w
    cw = 0.6 * fs / 72 / W; lh = 1.75 * fs / 72 / H
    cols = [max([tw(card['title'])] + [tw(r[0]) for r in rows]), max([tw(card.get('sub', ''))] + [tw(r[1]) for r in rows]),
            max([tw(card.get('pill', ''))] + [tw(r[2]) for r in rows])]
    wc = [c + 1.6 * cw for c in cols]; w = sum(wc)
    bb = ax.get_position(); x0 = bb.x1 - w - 0.006; y0 = bb.y1 - 0.008
    ht = '#131722' if lum(hc) > 165 else '#ffffff'
    add = lambda *a, **k: fig.patches.append(Rectangle(*a, transform=fig.transFigure, **k))
    add((x0, y0 - lh), w, lh, fc=hc, ec='none', zorder=10)
    fig.text(x0 + 0.8 * cw, y0 - lh / 2, card['title'], color=ht, fontsize=fs, family=fam, va='center', zorder=11)
    fig.text(x0 + wc[0] + 0.8 * cw, y0 - lh / 2, card.get('sub', ''), color=ht, fontsize=fs, family=fam, va='center', zorder=11)
    add((x0 + wc[0] + wc[1], y0 - lh), wc[2], lh, fc=th['body'], ec='none', zorder=10)
    fig.text(x0 + wc[0] + wc[1] + wc[2] / 2, y0 - lh / 2, card.get('pill', ''), color=hc if lum(hc) < 200 else th['fg'], fontsize=fs, family=fam, va='center', ha='center', zorder=11)
    for i, (a, v, ww, c) in enumerate(rows):
        yy = y0 - lh * (i + 2)
        add((x0, yy), w, lh, fc=th['body'], ec='none', zorder=10)
        fig.text(x0 + 0.8 * cw, yy + lh / 2, a, color=th['sub'], fontsize=fs, family=fam, va='center', zorder=11)
        fig.text(x0 + wc[0] + 0.8 * cw, yy + lh / 2, v, color=c or th['fg'], fontsize=fs, family=fam, va='center', zorder=11)
        fig.text(x0 + w - 0.8 * cw, yy + lh / 2, ww, color=c or th['fg'], fontsize=fs, family=fam, va='center', ha='right', zorder=11)
    add((x0, y0 - lh * (len(rows) + 1)), w, lh * (len(rows) + 1), fc='none', ec=hc, lw=1, alpha=0.7, zorder=12)


# ─────────────────────────────────────────────────────────── chart
def render(df, td, a, out_path):
    th = THEMES[a.theme]
    s, e = (T(*map(int, x.split(':'))) for x in (a.start, a.end))
    t0 = (td - pd.Timedelta(days=1) if s >= T(18, 0) else td) + pd.Timedelta(hours=s.hour, minutes=s.minute)
    t1 = (td - pd.Timedelta(days=1) if e >= T(18, 0) else td) + pd.Timedelta(hours=e.hour, minutes=e.minute)
    if t1 <= t0:
        sys.exit('--end must be after --start')
    day = df[df.td == td]
    if day.empty or day.dt.min() > t1:
        print(f'  {td:%Y-%m-%d}: no data in the window'); return False
    L = levels(df, td, t1)
    W = day[(day.dt >= t0) & (day.dt <= t1)]
    if len(W) < 2:
        print(f'  {td:%Y-%m-%d}: no data in the window'); return False
    B = W.groupby(W.dt.dt.floor(f'{a.tf}min')).agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last')).reset_index()
    nd = 2 if B.c.abs().max() > 50 else 4
    mob = a.layout == 'mobile'
    fig = plt.figure(figsize=(6.6, 11.6) if mob else (16, 9), dpi=120 if mob else 115, facecolor=th['bg'])
    ax = fig.add_axes([0.04, 0.05, 0.84, 0.86] if mob else [0.02, 0.06, 0.93, 0.86])
    ax.set_facecolor(th['bg'])
    bt = B.dt.values
    xf = lambda t: float(np.searchsorted(bt, np.datetime64(t)))                 # time → bar position (gaps safe)
    for i, r in B.iterrows():
        col = th['up'] if r.c >= r.o else th['dn']
        ax.plot([i, i], [r.l, r.h], color=col, lw=0.7 if not mob else 0.6, zorder=3)
        ax.add_patch(Rectangle((i - 0.32, min(r.o, r.c)), 0.64, max(abs(r.c - r.o), (B.h.max() - B.l.min()) * 0.001), fc=col, ec=col, lw=0.3, zorder=3))
    n = len(B); xr = n + max(8, n * 0.18)
    lo, hi = B.l.min(), B.h.max(); rg = hi - lo or 1
    fs = 7.4 if not mob else 7.0
    show = set(a.show.split(',')) if a.show else set()
    lines = []
    def hline(y, label, col, ls='-', x0=0, alpha=0.9):
        if np.isnan(y):
            return
        ax.plot([max(x0, 0), n - 1 + 2], [y, y], color=col, lw=1, ls=ls, alpha=alpha, zorder=2)
        lines.append((y, label, col))
    def box(ta, tb, h, l, col, label):
        if np.isnan(h):
            return
        xa, xb = xf(max(ta, B.dt.iloc[0])), xf(min(tb, B.dt.iloc[-1] + pd.Timedelta(minutes=a.tf)))
        if xb <= xa or xb < 0 or xa > n:
            hline(h, label + ' H', col, ':'); hline(l, label + ' L', col, ':'); return
        ax.add_patch(Rectangle((xa - 0.5, l), xb - xa, h - l, fc=col, alpha=0.08, ec=col, lw=0.8, zorder=1))
        ax.text(xa, h + rg * 0.01, label, color=col, fontsize=fs * 0.95, va='bottom', zorder=6)
        hline(h, label + ' H', col, ':', x0=xb); hline(l, label + ' L', col, ':', x0=xb)
    if 'pdhl' in show and 'pdh' in L:
        sfx = '' if L.get('same_contract', True) else ' (prev contract)'
        hline(L['pdh'], 'PDH' + sfx, C_PD, '--'); hline(L['pdl'], 'PDL' + sfx, C_PD, '--')
    if 'overnight' in show and not np.isnan(L['on'][0]):
        onh, onl = L['on']; hline((onh + onl) / 2, 'O/N mid', C_ON, '--')
        hline(onh, 'O/N H', C_ON, ':', alpha=0.6); hline(onl, 'O/N L', C_ON, ':', alpha=0.6)
    d0 = td - pd.Timedelta(days=1)
    if 'asia' in show and t1 > d0 + pd.Timedelta(hours=20):
        box(d0 + pd.Timedelta(hours=20), td, *L['asia'], C_ASIA, 'Asia')
    if 'london' in show and t1 > td + pd.Timedelta(hours=2):
        box(td + pd.Timedelta(hours=2), td + pd.Timedelta(hours=5), *L['ldn'], C_LDN, 'London')
    if 'or' in show and t1 > td + pd.Timedelta(hours=9, minutes=30):
        box(td + pd.Timedelta(hours=9, minutes=30), td + pd.Timedelta(hours=10), *L['or'], C_OR, 'OR')
    if 'open' in show:
        x930 = xf(td + pd.Timedelta(hours=9, minutes=30))
        if 0 <= x930 <= n:
            ax.axvline(x930 - 0.5, color=th['fg'], lw=0.8, ls=':', alpha=0.5, zorder=1)
    for spec in a.level or []:
        p = spec.split(':'); hline(float(p[0]), p[1] if len(p) > 1 else p[0], p[2] if len(p) > 2 else C_LVL, '-')
    # labels on the right, nudged apart so they never print over each other
    ylim_lo, ylim_hi = lo - rg * 0.08, hi + rg * (0.55 if not mob else 0.45)
    vis = sorted([l for l in lines if ylim_lo <= l[0] <= ylim_hi], key=lambda l: l[0])
    merged = []                                                 # levels at the same price share one label
    for y, lab, col in vis:
        if merged and abs(y - merged[-1][0]) < 1e-9:
            merged[-1][1].append(lab)
        else:
            merged.append([y, [lab], col])
    gap = rg * 0.028; placed = []
    for y, labs, col in merged:
        yy = max(y, placed[-1] + gap) if placed else y
        placed.append(yy)
        ax.text(n + 1.5, yy, f'{" · ".join(labs)} {fmt(y, nd)}', color=col, fontsize=fs * 0.9, va='center', zorder=6)
    ax.set_xlim(-2, xr); ax.set_ylim(ylim_lo, ylim_hi)
    ax.yaxis.tick_right(); ax.tick_params(colors=th['fg'], labelsize=fs, length=0); ax.grid(color=th['grid'], lw=0.6)
    for sp in ax.spines.values():
        sp.set_color(th['grid'])
    step = {1: 30, 2: 30, 3: 60, 5: 60, 10: 120, 15: 120}.get(a.tf, 240) if not mob else {1: 60}.get(a.tf, 120)
    tk = [i for i, t in enumerate(B.dt) if (t.hour * 60 + t.minute) % step == 0]
    ax.set_xticks(tk); ax.set_xticklabels([f'{B.dt[i]:%H:%M}' for i in tk])
    if a.card != 'none':
        card = json.load(open(a.card)) if a.card != 'auto' else auto_card(L, t1, nd)
        if mob:
            card = dict(card, rows=card['rows'][:5])
        draw_card(fig, ax, card, th, 7.2 if mob else 8.2, mono=not mob)
    title = a.title or f'{a.symbol or L["k"]} · {a.tf} min · {td:%a %d %b %Y} · {t0:%H:%M}–{t1:%H:%M} ET'
    fig.text(0.04 if mob else 0.02, 0.965, title, fontsize=9 if mob else 11, color=th['fg'], weight='bold')
    fig.savefig(out_path, facecolor=th['bg']); plt.close(fig)
    print(f'  wrote {out_path}')
    return True


def main():
    p = argparse.ArgumentParser(description='TradingView-style chart mock-ups from 1-minute CSVs (ET).')
    p.add_argument('--data', nargs='+', required=True, help='CSV file(s) or glob(s), quoted')
    p.add_argument('--date', default='last', help='YYYY-MM-DD, a range YYYY-MM-DD..YYYY-MM-DD, or "last"')
    p.add_argument('--start', default='07:00', help='window start, ET (18:00 or later = the evening before)')
    p.add_argument('--end', default='12:00', help='window end, ET; nothing after it is used')
    p.add_argument('--tf', type=int, default=1, help='candle minutes (1, 2, 3, 5, 15, ...)')
    p.add_argument('--show', default='pdhl,overnight,asia,london,or,open', help='overlays; "" for none')
    p.add_argument('--level', action='append', help='extra line PRICE[:LABEL[:#COLOR]], repeatable')
    p.add_argument('--card', default='auto', help='auto, none, or a card JSON file')
    p.add_argument('--theme', choices=THEMES, default='light')
    p.add_argument('--layout', choices=['desktop', 'mobile'], default='desktop')
    p.add_argument('--tz-shift', type=float, default=None, help='hours added to the file times (default: +1 for Barchart, 0 otherwise)')
    p.add_argument('--symbol', default=None, help='name shown in the title (default: the contract)')
    p.add_argument('--title', default=None)
    p.add_argument('--out', default=None, help='PNG path, or a folder for a date range (default: ./mockups/)')
    a = p.parse_args()
    df = load(a.data, a.tz_shift)
    days = sorted(df.td.unique())
    if a.date == 'last':
        sel = [days[-1]]
    elif '..' in a.date:
        d1, d2 = (pd.Timestamp(x) for x in a.date.split('..'))
        sel = [d for d in days if d1 <= d <= d2]
    else:
        sel = [pd.Timestamp(a.date)]
    if not sel:
        sys.exit('no trading days in the data for --date')
    single = len(sel) == 1 and a.out and a.out.lower().endswith('.png')
    folder = os.path.dirname(a.out) if single else (a.out or 'mockups')
    if folder:
        os.makedirs(folder, exist_ok=True)
    made = 0
    for td in sel:
        if td not in days:
            print(f'  {td:%Y-%m-%d}: not in the data'); continue
        k = df[df.td == td].k.iloc[0]
        path = a.out if single else os.path.join(folder, f'{k}_{td:%Y-%m-%d}_{a.end.replace(":", "")}.png')
        made += render(df, td, a, path)
    print(f'{made} mock-up(s) written')


if __name__ == '__main__':
    main()
