/*
 * TradeDialog.c  --  FTMO Hybrid Trading System
 *
 * A tiny Win32 helper DLL that renders a COLOUR-CODED approve/deny dialog for
 * the Phase-2 interactive tester harness (mql5/experts/HybridForwardTest.mq5).
 *
 * ---------------------------------------------------------------------------
 * POLL-DRIVEN, EDITABLE design (replaces the old single-call modal):
 *
 * The old ShowTradeDialog() ran its OWN blocking Win32 message loop, so MQL
 * code never ran while the dialog was up -> the chart could not update. The
 * editable-dialog feature needs the OPPOSITE: MQL must run between input
 * events so it can recompute R:R-locked levels and MOVE the real chart lines
 * live. So the dialog is now split into a poll-driven API that the EA drives
 * from a while-loop inside OnTick (the loop still blocks OnTick, so the tester
 * stays held on the bar -- the pause is preserved -- but MQL runs each turn):
 *
 *   int  TD_Open(title,symbol,strategy,direction, entry,sl,tp, lots,rr);
 *        -> create the window (Entry/SL/TP as EDIT boxes), return immediately.
 *           returns 1 ok, 0 on failure (caller should fail-closed = deny).
 *   int  TD_Poll(double* e,double* s,double* t,int* dirty);
 *        -> drain pending window messages (PeekMessage pump), read the current
 *           Entry/SL/TP edit-box text into the e,s,t out-params; set dirty=1
 *           changed a box since the last poll. Returns 0=pending, 1=Accept,
 *           2=Skip/closed.
 *   void TD_SetDisplay(e,s,t, lots,rr, ok);
 *        -> MQL pushes the RECOMPUTED, normalised strings back into the boxes
 *           (skipping whichever box currently has keyboard focus, so it never
 *           fights the user's cursor) + the lots/RR statics, and enables or
 *           disables the Accept button via `ok` (invalid geometry => disabled).
 *   void TD_Close(void);
 *        -> destroy the window and free per-dialog GDI objects.
 *
 * ALL numeric math (R:R lock, lot sizing, tick/stops normalisation) lives in
 * MQL where the broker-spec functions are; this DLL is display + text input +
 * message pump only. It never computes a price or a lot size.
 *
 * The old modal ShowTradeDialog() export is retained (unused by the EA now,
 * which drives the poll API) only so older binaries that still #import it keep
 * linking; the EA's non-coloured fallback path uses user32!MessageBoxW.
 *
 * Colours (RGB macro => correct 0x00BBGGRR COLORREF byte order):
 *     entry = green(0,160,0)  SL = red(204,0,0)  TP = blue(0,0,204)
 *     BUY = green   SELL = red
 *
 * Build (from WSL): see mql5/dll/build.sh  (zig cc -target x86_64-windows-gnu)
 */

#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>
#include <richedit.h>      /* events list is a RichEdit so <=12h items can be red+bold */
#ifndef MSFTEDIT_CLASS
#define MSFTEDIT_CLASS L"RICHEDIT50W"
#endif

/* ---- colour palette ----------------------------------------------------- */
#define COL_ENTRY   RGB(0,160,0)      /* green  */
#define COL_SL      RGB(204,0,0)      /* red    */
#define COL_TP      RGB(0,0,204)      /* blue   */
#define COL_BUY     RGB(0,160,0)
#define COL_SELL    RGB(204,0,0)
#define COL_LABEL   RGB(90,90,90)     /* field captions (grey)        */
#define COL_VALUE   RGB(20,20,20)     /* neutral values (near-black)  */
#define COL_BG      RGB(248,248,248)  /* dialog background            */
#define COL_EDITBG  RGB(255,255,255)  /* edit-box background (white)  */
#define COL_HINT    RGB(120,120,120)  /* status hint text             */

/* ---- control ids -------------------------------------------------------- */
#define ID_YES      100
#define ID_NO       101
#define ID_ENTRY    200   /* EDIT */
#define ID_SL       201   /* EDIT */
#define ID_TP       202   /* EDIT (single TP, or TP1 for scale-out) */
#define ID_TP2      206   /* EDIT (TP2 runner; only shown for scale-out) */
#define ID_LOTS     203   /* STATIC value */
#define ID_RR       204   /* STATIC value */
#define ID_ORDER    205   /* STATIC: order type to be placed (MARKET / pending) */
#define ID_HINT     210   /* STATIC status line */
#define ID_WHY      211   /* STATIC "Why skip?" prompt (reason-picker mode) */
#define ID_BACK     212   /* BUTTON: back out of the reason picker */
#define ID_EVENTS   213   /* read-only multiline EDIT: upcoming-events list */
#define ID_REDACT   214   /* BUTTON: toggle coach-mode redaction */
#define ID_EVTOGGLE 215   /* BUTTON: collapse/expand the events list */
#define ID_SHOT     216   /* BUTTON: manually re-take the H4 blind screenshot */
#define ID_REASON1  300   /* 6 reason buttons: ID_REASON1 + (code-1), code 1..6 */

/* ---- management-panel control ids (separate window, separate thread) ------ */
#define ID_MCLOSE   400   /* BUTTON: close full position (arm -> confirm)      */
#define ID_MCLOSE50 401   /* BUTTON: close 50% (arm -> confirm)                */
#define ID_MBE      402   /* BUTTON: SL -> break-even (instant; reversible)    */
#define ID_MSTATE   403   /* read-only multiline EDIT: live position state     */
#define PM_UPDATE   (WM_APP + 1)   /* EA pushed new state text (cross-thread)  */
#define PM_KILL     (WM_APP + 2)   /* EA asked to tear the panel down          */

/* ---- layout metrics (file-scope so relayout() shares them with TD_Open) --- */
#define L_PADX     22
#define L_PADY     18
#define L_LABELW   108
#define L_GAP      10
#define L_VALUEW   250
#define L_ROWH     30
#define L_EDITH    24
#define L_NROWS    9      /* sym,strat,dir,entry,sl,tp,lots,rr,order */
#define L_CLIENTW  (L_PADX + L_LABELW + L_GAP + L_VALUEW + L_PADX)
#define L_ROWSBOT  (L_PADY + L_NROWS * L_ROWH)
#define L_BTNW     130
#define L_BTNH     34
#define L_BTNGAP   20
#define L_HINTH    40
#define L_EVH      110    /* events list edit height when expanded */
#define L_RBTNH    30
#define L_RGAP     10
#define L_RBTNW    ((L_CLIENTW - 2*L_PADX - 2*L_RGAP) / 3)
#define L_REDH     28
#define L_TGLH     24     /* events collapse toggle button height */

static HINSTANCE g_hinst = NULL;
static HMODULE   g_riched = NULL;    /* Msftedit.dll, loaded once for the RichEdit class */
static LONG      g_inuse = 0;
static ATOM      g_cls   = 0;
static HBRUSH    g_bg    = NULL;   /* process-global; outlives every call */
static HBRUSH    g_edbg  = NULL;
static const wchar_t *CLS = L"HybridTradeDlg";

/* ---- persistent dialog state (poll-driven; lives between API calls) ----- */
typedef struct {
    HWND   hwnd;
    HWND   hEntry, hSl, hTp, hTp2; /* editable (hTp2 = NULL for single-target) */
    int    rows_bot;             /* Y where the field rows end (relayout anchor) */
    HWND   hSymbol;              /* symbol value static (redactable) */
    HWND   hLots,  hRr;          /* recomputed statics */
    HWND   hOrder;               /* "MARKET" / "BUY LIMIT @ x" (set by EA) */
    HWND   hEvents;              /* upcoming-events list (read-only multiline) */
    HWND   hEvToggle;            /* collapse/expand the events list */
    HWND   hRedact;              /* coach-mode toggle button */
    HWND   hHint;
    HWND   hYes,   hNo;
    HWND   hShot;               /* "Retake H4 screenshot" button */
    int    shot_req;            /* 1 = operator asked to re-take the H4 screenshot */
    HWND   hWhy;                /* "Why skip?" prompt (reason-picker mode) */
    HWND   hReason[6];          /* labelled reason buttons (codes 1..6) */
    HWND   hBack;               /* back out of the reason picker */
    int    events_open;         /* 1 = events list expanded */
    DWORD  style, exstyle;      /* window styles (for relayout's AdjustWindowRectEx) */
    int    redact;              /* 1 = coach mode: symbol hidden, dates relative */
    wchar_t sym[64];            /* real symbol (restored when redaction off) */
    wchar_t evAbs[4096];        /* events list, absolute dates */
    wchar_t evRel[4096];        /* events list, relative dates */
    HFONT  fNormal, fBold, fCaption, fHint;
    int    result;              /* -1 pending, 1 accept, 2 skip */
    int    skip_reason;         /* 1-6 skip-reason code (set on skip; 6 = other) */
    int    reason_mode;         /* 1 = reason picker showing (Accept/Skip hidden) */
    int    dirty;               /* user edited a box since last poll */
    int    suppress;            /* ignore EN_CHANGE we cause ourselves */
    int    ok;                  /* Accept currently allowed (valid geometry) */
    int    isBuy;
    double lastE, lastS, lastT; /* last good parsed values (parse fallback) */
} DlgState;

static DlgState g;              /* single active dialog */

/* ---- management panel: a SEPARATE, non-modal window on its OWN thread ------
 * The signal dialog above is pumped by the EA's blocking OnTick loop, so it
 * dies the instant the tester pauses. The management panel must do the OPPOSITE:
 * stay live + clickable WHILE the visual tester is paused (that is exactly when
 * the operator decides at bar close). A tester pause stops OnTick, so nothing on
 * the MQL side could pump it - therefore the panel runs its own GetMessage loop
 * on a dedicated thread. The EA never touches these HWNDs; it only:
 *   - TDM_Open  : spawn the thread + window, return once it exists
 *   - TDM_Update: copy state text under a lock + PostMessage (never a
 *                 cross-thread SetWindowTextW), set the BE-button enable
 *   - TDM_Poll  : drain a latched button action (InterlockedExchange) and
 *                 execute the actual trade op on the MQL thread (trading must
 *                 not happen in this UI thread)
 *   - TDM_Close : PostMessage a kill + join the thread
 * Close / Close-50% arm on the first click (caption -> "Confirm ...") and fire
 * on the second; SL->BE is instant (reversible). Arming disarms if the volume
 * changes under it (an auto scale-out fired), so a stale confirm can't fire.  */
typedef struct {
    HANDLE  hThread;
    HANDLE  hReady;             /* signaled once the window+controls exist   */
    HWND    hwnd;
    HWND    hState;             /* read-only multiline: direction/lots/R/... */
    HWND    hClose, hClose50, hBe;
    volatile LONG action;       /* 0 none, 1 close, 2 close50, 3 be (latched)*/
    volatile LONG be_enable;    /* 1 = BE button clickable (valid stop dist)  */
    CRITICAL_SECTION cs;        /* guards buf + lots                          */
    wchar_t buf[1024];          /* pending state text (EA -> panel)           */
    double  lots;               /* current volume (EA -> panel)               */
    double  last_lots;          /* panel-thread: last seen volume (disarm)    */
    int     armed;              /* panel-thread only: 0 / 1(close) / 2(c50)   */
    int     inited;             /* cs initialised (teardown guard)            */
    wchar_t title[128];
    HFONT   fBody, fBtn;
} PanelState;
static PanelState gm;

/* ------------------------------------------------------------------------ */
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) { g_hinst = h; DisableThreadLibraryCalls(h); }
    return TRUE;
}

static HFONT make_font(int height, int weight)
{
    return CreateFontW(height, 0, 0, 0, weight, FALSE, FALSE, FALSE,
                       DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                       CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
}

/* read an edit box -> double; on unparseable/empty text return the fallback */
static double read_edit(HWND h, double fallback)
{
    wchar_t buf[64];
    int n = GetWindowTextW(h, buf, 63);
    if (n <= 0) return fallback;
    /* strip spaces */
    wchar_t clean[64]; int j = 0;
    for (int i = 0; buf[i] && j < 63; i++)
        if (buf[i] != L' ' && buf[i] != L'\t') clean[j++] = buf[i];
    clean[j] = 0;
    if (j == 0) return fallback;
    wchar_t *end = NULL;
    double v = wcstod(clean, &end);
    if (end == clean) return fallback;      /* nothing parsed */
    return v;
}

/* set an edit box's text WITHOUT triggering our own dirty flag, and never
   while it holds keyboard focus (so we don't stomp the user's cursor)       */
static void set_edit(HWND h, const wchar_t *txt, HWND focus)
{
    if (!txt) return;
    if (h == focus) return;                 /* user is typing here - leave it */
    wchar_t cur[64];
    GetWindowTextW(h, cur, 63);
    if (wcscmp(cur, txt) == 0) return;      /* no change - avoid flicker */
    g.suppress = 1;
    SetWindowTextW(h, txt);
    g.suppress = 0;
}

/* coach mode: hide the symbol + switch the events list to relative dates, so a
   screenshot given to the training advisor can't be reverse-identified to a
   historical instance (and thus can't be "graded" with hindsight).            */
/* Fill the events list, painting anything due in <=12h RED + BOLD. Imminence
   comes from the RELATIVE form ("in Xd Yh") which is line-for-line aligned with
   whichever form is shown (abs/rel). Each line is appended under its own char
   format, so it's robust to how RichEdit stores line breaks. */
static void set_events_text(void)
{
    if (!g.hEvents) return;
    const wchar_t *dp = g.redact ? g.evRel : g.evAbs;   /* displayed text  */
    const wchar_t *rp = g.evRel;                          /* imminence source */
    SendMessageW(g.hEvents, EM_SETREADONLY, FALSE, 0);
    SetWindowTextW(g.hEvents, L"");
    CHARFORMAT2W cf; ZeroMemory(&cf, sizeof(cf)); cf.cbSize = sizeof(cf);
    while (dp && *dp) {
        int dlen = 0; while (dp[dlen] && dp[dlen] != L'\r' && dp[dlen] != L'\n') dlen++;
        int rlen = 0; while (rp[rlen] && rp[rlen] != L'\r' && rp[rlen] != L'\n') rlen++;
        int dd = -1, hh = -1;
        int imm = (swscanf(rp, L"in %dd %dh", &dd, &hh) == 2 && dd == 0 && hh <= 12);
        cf.dwMask     = CFM_COLOR | CFM_BOLD;
        cf.crTextColor = imm ? COL_SL : COL_VALUE;
        cf.dwEffects  = imm ? CFE_BOLD : 0;
        SendMessageW(g.hEvents, EM_SETSEL, 0x7fffffff, 0x7fffffff);   /* caret -> end */
        SendMessageW(g.hEvents, EM_SETCHARFORMAT, SCF_SELECTION, (LPARAM)&cf);
        wchar_t buf[512]; int n = dlen < 500 ? dlen : 500;
        wcsncpy(buf, dp, n); buf[n] = 0; wcscat(buf, L"\r\n");
        SendMessageW(g.hEvents, EM_REPLACESEL, FALSE, (LPARAM)buf);
        dp += dlen; while (*dp == L'\r' || *dp == L'\n') dp++;
        rp += rlen; while (*rp == L'\r' || *rp == L'\n') rp++;
    }
    SendMessageW(g.hEvents, EM_SETREADONLY, TRUE, 0);
    SendMessageW(g.hEvents, EM_SETSEL, 0, 0);
    SendMessageW(g.hEvents, WM_VSCROLL, SB_TOP, 0);
}

static void apply_redact(void)
{
    if (g.hSymbol)
        SetWindowTextW(g.hSymbol, g.redact ? L"██████" : g.sym);
    set_events_text();
    if (g.hRedact)
        SetWindowTextW(g.hRedact, g.redact
            ? L"Coach mode: ON  (symbol + dates hidden)"
            : L"Coach mode: OFF  (real symbol + dates)");
}

/* show/hide the reason picker. Clicking Skip never records "6" silently any
   more - it swaps Accept/Skip for six labelled reason buttons and grows the
   window, so an intentional skip always captures a reason.                   */
/* Single source of truth for control positions + window height. Reads the two
   state flags (events_open, reason_mode) and lays everything out top-to-bottom,
   moving each control and resizing the window to fit. Called on open and on any
   toggle, so the events-collapse and reason-picker states compose cleanly.     */
static void relayout(void)
{
    if (!g.hwnd) return;
    int W = L_CLIENTW;
    int y = g.rows_bot + 6;

    /* events collapse toggle (always visible) */
    MoveWindow(g.hEvToggle, L_PADX, y, W - 2 * L_PADX, L_TGLH, TRUE);
    SetWindowTextW(g.hEvToggle, g.events_open
        ? L"▼  Upcoming events (next 2 weeks)"
        : L"▶  Upcoming events (next 2 weeks)");
    y += L_TGLH + 4;

    if (g.events_open) {
        ShowWindow(g.hEvents, SW_SHOW);
        MoveWindow(g.hEvents, L_PADX, y, W - 2 * L_PADX, L_EVH, TRUE);
        y += L_EVH + 8;
    } else {
        ShowWindow(g.hEvents, SW_HIDE);
        y += 2;
    }

    /* coach-mode toggle */
    MoveWindow(g.hRedact, (W - 300) / 2, y, 300, L_REDH, TRUE);
    y += L_REDH + 8;

    /* hint (two lines) */
    MoveWindow(g.hHint, L_PADX, y, W - 2 * L_PADX, L_HINTH, TRUE);
    y += L_HINTH + 4;

    int btnTop = y;
    if (!g.reason_mode) {
        ShowWindow(g.hWhy, SW_HIDE); ShowWindow(g.hBack, SW_HIDE);
        for (int i = 0; i < 6; i++) ShowWindow(g.hReason[i], SW_HIDE);
        ShowWindow(g.hYes, SW_SHOW); ShowWindow(g.hNo, SW_SHOW);
        int bx = (W - (L_BTNW * 2 + L_BTNGAP)) / 2;
        MoveWindow(g.hYes, bx, btnTop, L_BTNW, L_BTNH, TRUE);
        MoveWindow(g.hNo,  bx + L_BTNW + L_BTNGAP, btnTop, L_BTNW, L_BTNH, TRUE);
        y = btnTop + L_BTNH;
        /* full-width "Retake H4 screenshot" row under Accept/Skip */
        ShowWindow(g.hShot, SW_SHOW);
        MoveWindow(g.hShot, bx, y + L_RGAP, L_BTNW * 2 + L_BTNGAP, L_RBTNH, TRUE);
        y += L_RGAP + L_RBTNH;
    } else {
        ShowWindow(g.hYes, SW_HIDE); ShowWindow(g.hNo, SW_HIDE);
        ShowWindow(g.hShot, SW_HIDE);
        ShowWindow(g.hWhy, SW_SHOW); ShowWindow(g.hBack, SW_SHOW);
        MoveWindow(g.hWhy, L_PADX, btnTop + 4, W - 2 * L_PADX, 20, TRUE);
        int rTop1 = btnTop + 28;
        int rTop2 = rTop1 + L_RBTNH + L_RGAP;
        for (int i = 0; i < 6; i++) {
            ShowWindow(g.hReason[i], SW_SHOW);
            int col = i % 3, row = i / 3;
            MoveWindow(g.hReason[i], L_PADX + col * (L_RBTNW + L_RGAP),
                       (row == 0) ? rTop1 : rTop2, L_RBTNW, L_RBTNH, TRUE);
        }
        int backTop = rTop2 + L_RBTNH + 10;
        MoveWindow(g.hBack, (W - 120) / 2, backTop, 120, L_BTNH, TRUE);
        y = backTop + L_BTNH;
    }

    int clientH = y + L_PADY;
    RECT rc = { 0, 0, W, clientH };
    AdjustWindowRectEx(&rc, g.style, FALSE, g.exstyle);
    SetWindowPos(g.hwnd, NULL, 0, 0, rc.right - rc.left, rc.bottom - rc.top,
                 SWP_NOMOVE | SWP_NOZORDER);
}

static void show_reason_picker(int on)
{
    g.reason_mode = on ? 1 : 0;
    relayout();
    SetFocus(on ? g.hReason[0] : g.hNo);
}

/* ------------------------------------------------------------------------ */
static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wp; HWND hc = (HWND)lp;
        int id = GetDlgCtrlID(hc);
        COLORREF c = COL_VALUE;
        if (id == ID_ENTRY) c = COL_ENTRY;
        else if (id == ID_SL) c = COL_SL;
        else if (id == ID_TP || id == ID_TP2) c = COL_TP;
        SetTextColor(hdc, c);
        SetBkColor(hdc, COL_EDITBG);
        return (LRESULT)(g_edbg ? g_edbg : (HBRUSH)GetStockObject(WHITE_BRUSH));
    }
    case WM_CTLCOLORSTATIC: {
        HDC hdc = (HDC)wp; HWND hc = (HWND)lp;
        int id = GetDlgCtrlID(hc);
        if (id == ID_EVENTS) {                 /* read-only multiline list: white box */
            SetTextColor(hdc, COL_VALUE);
            SetBkColor(hdc, COL_EDITBG);
            return (LRESULT)(g_edbg ? g_edbg : (HBRUSH)GetStockObject(WHITE_BRUSH));
        }
        COLORREF c = COL_LABEL;
        if (id == ID_ENTRY)               c = COL_ENTRY; /* entry: green, matches chart line */
        else if (id == ID_LOTS || id == ID_RR || id == ID_ORDER) c = COL_VALUE;
        else if (id == ID_WHY)            c = COL_SL;    /* prompt: attention red */
        else if (id == ID_HINT)           c = COL_HINT;
        else if (id >= 1000)              c = (COLORREF)(id - 1000); /* value statics carry colour in id offset */
        SetBkMode(hdc, TRANSPARENT);
        SetTextColor(hdc, c);
        return (LRESULT)(g_bg ? g_bg : (HBRUSH)GetStockObject(WHITE_BRUSH));
    }
    case WM_CTLCOLORBTN:
    case WM_CTLCOLORDLG:
        return (LRESULT)(g_bg ? g_bg : (HBRUSH)GetStockObject(WHITE_BRUSH));

    case WM_COMMAND: {
        int id = LOWORD(wp);
        if (id == ID_YES) { if (g.ok) g.result = 1; return 0; }
        if (id == ID_NO)  { show_reason_picker(1); return 0; }   /* Skip -> ask why */
        if (id == ID_BACK){ show_reason_picker(0); return 0; }   /* cancel the picker */
        if (id == ID_REDACT){ g.redact ^= 1; apply_redact(); return 0; }
        if (id == ID_EVTOGGLE){ g.events_open ^= 1; relayout(); return 0; }
        if (id == ID_SHOT){ g.shot_req = 1;
            SetWindowTextW(g.hHint, L"Retaking the H4 screenshot - it will overwrite the "
                                    L"advisor's h4.png in a moment."); return 0; }
        if (id >= ID_REASON1 && id < ID_REASON1 + 6) {
            g.result = 2; g.skip_reason = id - ID_REASON1 + 1; return 0;
        }
        if (HIWORD(wp) == EN_CHANGE && !g.suppress &&
            (id == ID_ENTRY || id == ID_SL || id == ID_TP || id == ID_TP2)) g.dirty = 1;
        break;
    }

    case WM_CLOSE:
        g.result = 2; g.skip_reason = 6;     /* [X]/Alt+F4 => hard skip ("other") */
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

/* colour-carrying static: a value static whose id encodes its text colour so
   WM_CTLCOLORSTATIC can recover it (1000 + colour). Used for Symbol/Strategy/
   Direction where each row wants its own colour.                             */
static HWND make_value_static(HWND parent, const wchar_t *txt, int x, int y,
                              int w, int h, COLORREF col, HFONT font)
{
    HWND s = CreateWindowExW(0, L"STATIC", txt ? txt : L"",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        x, y, w, h, parent, (HMENU)(INT_PTR)(1000 + (int)col), g_hinst, NULL);
    SendMessageW(s, WM_SETFONT, (WPARAM)font, TRUE);
    return s;
}

/* ------------------------------------------------------------------------ */
__declspec(dllexport)
int TD_Open(const wchar_t *title,   const wchar_t *symbol,
            const wchar_t *strategy, const wchar_t *direction,
            const wchar_t *sigtime, const wchar_t *entry,
            const wchar_t *sl,      const wchar_t *tp,
            const wchar_t *tp2,     const wchar_t *lots,
            const wchar_t *rr)
{
    if (InterlockedCompareExchange(&g_inuse, 1, 0) != 0) {
        OutputDebugStringW(L"[TradeDialog] TD_Open while busy -> ignored\n");
        return 0;
    }
    ZeroMemory(&g, sizeof(g));
    g.result   = -1;
    g.ok       = 1;
    g.isBuy    = (direction && (direction[0] == L'B' || direction[0] == L'b'));
    wcsncpy(g.sym, symbol ? symbol : L"", 63); g.sym[63] = 0;
    g.evAbs[0] = 0; g.evRel[0] = 0;

    g.fNormal  = make_font(-18, FW_NORMAL);
    g.fBold    = make_font(-18, FW_BOLD);
    g.fCaption = make_font(-16, FW_NORMAL);
    g.fHint    = make_font(-14, FW_NORMAL);

    if (g_bg   == NULL) g_bg   = CreateSolidBrush(COL_BG);
    if (g_edbg == NULL) g_edbg = CreateSolidBrush(COL_EDITBG);

    if (g_cls == 0) {
        WNDCLASSEXW wc; ZeroMemory(&wc, sizeof(wc));
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = WndProc;
        wc.hInstance = g_hinst;
        wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
        wc.hbrBackground = g_bg;
        wc.lpszClassName = CLS;
        g_cls = RegisterClassExW(&wc);
    }

    /* --- geometry: field-row metrics only. Everything BELOW the rows is placed
       (and the window sized) by relayout(), which also handles the events-
       collapse and reason-picker states. --- */
    const int PADX=L_PADX, LABELW=L_LABELW, GAP=L_GAP, VALUEW=L_VALUEW;
    const int ROWH=L_ROWH, EDITH=L_EDITH, rowsTop=L_PADY;

    /* NOT topmost + a minimise box: the operator can background/minimise the
       dialog (the tester stays paused; TD_Poll keeps pumping). WS_EX_APPWINDOW
       gives it a taskbar button so it can be restored. */
    g.style   = WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    g.exstyle = WS_EX_DLGMODALFRAME | WS_EX_APPWINDOW;
    g.events_open = 1;                                   /* events list expanded by default */

    int winW0 = L_CLIENTW + 16, winH0 = 480;             /* nominal; relayout() resizes */
    int x  = (GetSystemMetrics(SM_CXSCREEN) - winW0) / 2;
    int yy = 40;                                         /* near the top so a tall dialog fits */
    HWND hwnd = CreateWindowExW(g.exstyle, CLS, title ? title : L"Trade Signal",
                                g.style, x, yy, winW0, winH0, NULL, NULL, g_hinst, NULL);
    if (!hwnd) {
        DeleteObject(g.fNormal); DeleteObject(g.fBold);
        DeleteObject(g.fCaption); DeleteObject(g.fHint);
        InterlockedExchange(&g_inuse, 0);
        return 0;
    }
    g.hwnd = hwnd;

    int vx = PADX + LABELW + GAP;
    int scaleout = (tp2 && tp2[0]);          /* non-empty tp2 => two editable TP fields */
    DWORD est = WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL | ES_LEFT;
    int r = rowsTop;
    #define CAP(txt) do { HWND _c = CreateWindowExW(0, L"STATIC", (txt), \
        WS_CHILD | WS_VISIBLE | SS_LEFT, PADX, r, LABELW, ROWH - 6, hwnd, NULL, g_hinst, NULL); \
        SendMessageW(_c, WM_SETFONT, (WPARAM)g.fCaption, TRUE); } while (0)
    #define VSTAT(id, txt) CreateWindowExW(0, L"STATIC", (txt), WS_CHILD | WS_VISIBLE | SS_LEFT, \
        vx, r, VALUEW, ROWH - 6, hwnd, (HMENU)(INT_PTR)(id), g_hinst, NULL)

    CAP(L"Symbol");    g.hSymbol = make_value_static(hwnd, symbol, vx, r, VALUEW, ROWH - 6, COL_VALUE, g.fNormal); r += ROWH;
    CAP(L"Strategy");  make_value_static(hwnd, strategy, vx, r, VALUEW, ROWH - 6, COL_VALUE, g.fNormal);          r += ROWH;
    CAP(L"Direction"); make_value_static(hwnd, direction, vx, r, VALUEW, ROWH - 6,
                       g.isBuy ? COL_BUY : COL_SELL, g.fBold);                                                   r += ROWH;
    int is_fri = (sigtime && wcsstr(sigtime, L"Friday") != NULL);   /* Friday -> red + bold */
    CAP(L"Time");      make_value_static(hwnd, sigtime, vx, r, VALUEW, ROWH - 6,
                       is_fri ? COL_SL : COL_VALUE, is_fri ? g.fBold : g.fNormal);                                r += ROWH;

    /* Entry / SL / TP(s) are ALL editable + independent. Green/red/blue tie each
       to its chart line. Scale-out strategies split TP into TP1 (bank) + TP2. */
    CAP(L"Entry"); g.hEntry = CreateWindowExW(0, L"EDIT", entry ? entry : L"", est,
        vx, r + 1, 150, EDITH, hwnd, (HMENU)(INT_PTR)ID_ENTRY, g_hinst, NULL);
    SendMessageW(g.hEntry, WM_SETFONT, (WPARAM)g.fBold, TRUE);
    SendMessageW(g.hEntry, EM_SETLIMITTEXT, (WPARAM)24, 0);                                r += ROWH;
    CAP(L"Stop Loss"); g.hSl = CreateWindowExW(0, L"EDIT", sl ? sl : L"", est,
        vx, r + 1, 150, EDITH, hwnd, (HMENU)(INT_PTR)ID_SL, g_hinst, NULL);
    SendMessageW(g.hSl, WM_SETFONT, (WPARAM)g.fBold, TRUE);
    SendMessageW(g.hSl, EM_SETLIMITTEXT, (WPARAM)24, 0);                                   r += ROWH;
    CAP(scaleout ? L"TP1 (bank)" : L"Take Profit");
    g.hTp = CreateWindowExW(0, L"EDIT", tp ? tp : L"", est,
        vx, r + 1, 150, EDITH, hwnd, (HMENU)(INT_PTR)ID_TP, g_hinst, NULL);
    SendMessageW(g.hTp, WM_SETFONT, (WPARAM)g.fBold, TRUE);
    SendMessageW(g.hTp, EM_SETLIMITTEXT, (WPARAM)24, 0);                                   r += ROWH;
    if (scaleout) {
        CAP(L"TP2 (runner)");
        g.hTp2 = CreateWindowExW(0, L"EDIT", tp2, est,
            vx, r + 1, 150, EDITH, hwnd, (HMENU)(INT_PTR)ID_TP2, g_hinst, NULL);
        SendMessageW(g.hTp2, WM_SETFONT, (WPARAM)g.fBold, TRUE);
        SendMessageW(g.hTp2, EM_SETLIMITTEXT, (WPARAM)24, 0);                              r += ROWH;
    } else g.hTp2 = NULL;

    CAP(L"Lot size"); g.hLots = VSTAT(ID_LOTS, lots ? lots : L"");
    SendMessageW(g.hLots, WM_SETFONT, (WPARAM)g.fNormal, TRUE);                            r += ROWH;
    CAP(L"R : R");    g.hRr = VSTAT(ID_RR, rr ? rr : L"");
    SendMessageW(g.hRr, WM_SETFONT, (WPARAM)g.fNormal, TRUE);                              r += ROWH;
    CAP(L"Order");    g.hOrder = VSTAT(ID_ORDER, L"MARKET");
    SendMessageW(g.hOrder, WM_SETFONT, (WPARAM)g.fBold, TRUE);                             r += ROWH;
    #undef CAP
    #undef VSTAT
    g.rows_bot = r;

    /* --- controls below the field rows are created at nominal positions;
       relayout() (called at the end) places them + sizes the window. --- */
    g.hEvToggle = CreateWindowExW(0, L"BUTTON", L"▼  Upcoming events (next 2 weeks)",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 10, L_TGLH, hwnd, (HMENU)(INT_PTR)ID_EVTOGGLE, g_hinst, NULL);
    SendMessageW(g.hEvToggle, WM_SETFONT, (WPARAM)g.fHint, TRUE);
    if (!g_riched) g_riched = LoadLibraryW(L"Msftedit.dll");   /* RichEdit class */
    g.hEvents = CreateWindowExW(0, MSFTEDIT_CLASS, L"(loading...)",
        WS_CHILD | WS_VISIBLE | WS_BORDER | WS_VSCROLL |
        ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_LEFT,
        0, 0, 10, L_EVH, hwnd, (HMENU)(INT_PTR)ID_EVENTS, g_hinst, NULL);
    if (!g.hEvents)     /* fallback: plain EDIT (list works, no <=12h highlight) */
        g.hEvents = CreateWindowExW(0, L"EDIT", L"(loading...)",
            WS_CHILD | WS_VISIBLE | WS_BORDER | WS_VSCROLL |
            ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_LEFT,
            0, 0, 10, L_EVH, hwnd, (HMENU)(INT_PTR)ID_EVENTS, g_hinst, NULL);
    SendMessageW(g.hEvents, WM_SETFONT, (WPARAM)g.fHint, TRUE);
    SendMessageW(g.hEvents, EM_SETBKGNDCOLOR, 0, (LPARAM)COL_EDITBG);   /* RichEdit bg */
    g.hRedact = CreateWindowExW(0, L"BUTTON", L"Coach mode: OFF  (real symbol + dates)",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 300, L_REDH, hwnd, (HMENU)(INT_PTR)ID_REDACT, g_hinst, NULL);
    SendMessageW(g.hRedact, WM_SETFONT, (WPARAM)g.fHint, TRUE);

    g.hHint = CreateWindowExW(0, L"STATIC",
        L"Edit Entry / SL / TP (independent; R:R recomputes, Accept blocks below the floor).\n"
        L"Skip asks you why (pick a reason button, or press 1-6).",
        WS_CHILD | WS_VISIBLE | SS_LEFT, 0, 0, 10, L_HINTH,
        hwnd, (HMENU)(INT_PTR)ID_HINT, g_hinst, NULL);
    SendMessageW(g.hHint, WM_SETFONT, (WPARAM)g.fHint, TRUE);

    g.hYes = CreateWindowExW(0, L"BUTTON", L"&Accept",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, L_BTNW, L_BTNH, hwnd, (HMENU)(INT_PTR)ID_YES, g_hinst, NULL);
    g.hNo = CreateWindowExW(0, L"BUTTON", L"&Skip",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, L_BTNW, L_BTNH, hwnd, (HMENU)(INT_PTR)ID_NO, g_hinst, NULL);
    SendMessageW(g.hYes, WM_SETFONT, (WPARAM)g.fNormal, TRUE);
    SendMessageW(g.hNo,  WM_SETFONT, (WPARAM)g.fNormal, TRUE);

    /* manual re-screenshot: overwrites the advisor's h4.png for THIS setup. The
       OS-capture daemon watches for the EA-written trigger and re-grabs the
       tester window. Handy if the auto-capture came out wrong. */
    g.shot_req = 0;
    g.hShot = CreateWindowExW(0, L"BUTTON", L"Retake H4 screenshot",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, L_BTNW, L_RBTNH, hwnd, (HMENU)(INT_PTR)ID_SHOT, g_hinst, NULL);
    SendMessageW(g.hShot, WM_SETFONT, (WPARAM)g.fHint, TRUE);

    /* --- reason picker (hidden until Skip is clicked) --- */
    g.hWhy = CreateWindowExW(0, L"STATIC", L"Why skip this setup?  (or press 1-6)",
        WS_CHILD | SS_CENTER, 0, 0, 10, 20, hwnd, (HMENU)(INT_PTR)ID_WHY, g_hinst, NULL);
    SendMessageW(g.hWhy, WM_SETFONT, (WPARAM)g.fBold, TRUE);
    const wchar_t *rlab[6] = { L"1  Counter-trend", L"2  News / event",
                               L"3  Ugly structure", L"4  Target blocked",
                               L"5  Correlated", L"6  Gut / other" };
    for (int i = 0; i < 6; i++) {
        g.hReason[i] = CreateWindowExW(0, L"BUTTON", rlab[i],
            WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
            0, 0, L_RBTNW, L_RBTNH, hwnd, (HMENU)(INT_PTR)(ID_REASON1 + i), g_hinst, NULL);
        SendMessageW(g.hReason[i], WM_SETFONT, (WPARAM)g.fHint, TRUE);
    }
    g.hBack = CreateWindowExW(0, L"BUTTON", L"< Back",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 120, L_BTNH, hwnd, (HMENU)(INT_PTR)ID_BACK, g_hinst, NULL);
    SendMessageW(g.hBack, WM_SETFONT, (WPARAM)g.fNormal, TRUE);

    /* seed parse fallbacks from the initial strings */
    g.lastE = read_edit(g.hEntry, 0.0);
    g.lastS = read_edit(g.hSl,    0.0);
    g.lastT = read_edit(g.hTp,    0.0);

    relayout();                     /* position every control + size the window */
    ShowWindow(hwnd, SW_SHOW);
    SetForegroundWindow(hwnd);
    SetFocus(g.hNo);                /* default focus = Skip (safety) */
    return 1;
}

/* ------------------------------------------------------------------------ */
__declspec(dllexport)
int TD_Poll(double *entry, double *sl, double *tp, double *tp2, int *dirty)
{
    if (!g.hwnd) { if (dirty) *dirty = 0; return 2; }   /* not open => treat as skip */

    MSG msg; int guard = 0;
    while (guard++ < 256 && PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        if (msg.message == WM_KEYDOWN) {
            HWND f = GetFocus();
            int in_edit = (f == g.hEntry || f == g.hSl || f == g.hTp || f == g.hTp2);
            if (msg.wParam == VK_ESCAPE) {
                if (g.reason_mode)   show_reason_picker(0);   /* cancel the picker */
                else if (!in_edit)   show_reason_picker(1);   /* Esc = Skip = ask why */
            }
            else if (msg.wParam == VK_RETURN) { if (!g.reason_mode && g.ok) g.result = 1; }
            /* digit 1-6 = skip with that reason (works in either mode), but ONLY
               when not typing in a number box (there, digits are the SL/TP value). */
            else if (!in_edit && msg.wParam >= '1' && msg.wParam <= '6') {
                g.result = 2; g.skip_reason = (int)(msg.wParam - '0'); continue;
            }
            else if (!in_edit && msg.wParam >= VK_NUMPAD1 && msg.wParam <= VK_NUMPAD6) {
                g.result = 2; g.skip_reason = (int)(msg.wParam - VK_NUMPAD0); continue;
            }
        }
        if (!IsDialogMessageW(g.hwnd, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }

    double e = read_edit(g.hEntry, g.lastE);
    double s = read_edit(g.hSl,    g.lastS);
    double t = read_edit(g.hTp,    g.lastT);
    g.lastE = e; g.lastS = s; g.lastT = t;
    if (entry) *entry = e;
    if (sl)    *sl    = s;
    if (tp)    *tp    = t;
    if (tp2)   *tp2   = g.hTp2 ? read_edit(g.hTp2, *tp2) : *tp2;   /* single-target: unchanged */
    if (dirty) { *dirty = g.dirty ? 1 : 0; }
    g.dirty = 0;

    return (g.result < 0) ? 0 : g.result;
}

/* ------------------------------------------------------------------------ */
__declspec(dllexport)
void TD_SetDisplay(const wchar_t *entry, const wchar_t *sl, const wchar_t *tp,
                   const wchar_t *tp2, const wchar_t *lots,  const wchar_t *rr, int ok)
{
    if (!g.hwnd) return;
    HWND focus = GetFocus();
    set_edit(g.hEntry, entry, focus);
    set_edit(g.hSl,    sl,    focus);
    set_edit(g.hTp,    tp,    focus);
    if (g.hTp2) set_edit(g.hTp2, tp2, focus);
    if (lots) SetWindowTextW(g.hLots, lots);
    if (rr)   SetWindowTextW(g.hRr,   rr);
    g.ok = ok ? 1 : 0;
    EnableWindow(g.hYes, g.ok ? TRUE : FALSE);
    SetWindowTextW(g.hHint, g.ok
        ? L"Edit Entry / SL / TP (independent; R:R recomputes, Accept blocks below the floor).\n"
          L"Skip asks you why (pick a reason button, or press 1-6)."
        : L"Accept disabled - fix the value in red (bad SL/Entry/TP order, or R:R below min).\n"
          L"Skip asks you why (pick a reason button, or press 1-6).");
}

/* ------------------------------------------------------------------------ */
__declspec(dllexport)
void TD_Close(void)
{
    if (g.hwnd) { DestroyWindow(g.hwnd); g.hwnd = NULL; }
    if (g.fNormal)  { DeleteObject(g.fNormal);  g.fNormal  = NULL; }
    if (g.fBold)    { DeleteObject(g.fBold);    g.fBold    = NULL; }
    if (g.fCaption) { DeleteObject(g.fCaption); g.fCaption = NULL; }
    if (g.fHint)    { DeleteObject(g.fHint);    g.fHint    = NULL; }
    InterlockedExchange(&g_inuse, 0);
}

/* ------------------------------------------------------------------------ */
/* Skip-reason code from the last skip (1-6; 6 = bare Skip/Esc/close). Valid */
/* after TD_Poll returns 2; read before or after TD_Close (state persists    */
/* until the next TD_Open zeroes it).                                        */
__declspec(dllexport)
int TD_SkipReason(void) { return g.skip_reason; }

/* ------------------------------------------------------------------------ */
/* Coach-mode state (1 = redacted). The EA polls this to date-scrub the CHART
   corner label to match the dialog's redaction (the chart is EA-drawn, so the
   DLL toggle alone can't touch it).                                          */
__declspec(dllexport)
int TD_Coach(void) { return g.redact; }

/* ------------------------------------------------------------------------ */
/* 1 (once) if the operator clicked "Retake H4 screenshot" since the last    */
/* poll - the EA then writes the recapture trigger the OS-capture daemon     */
/* watches. Read-and-clear so each click fires exactly one re-shot.          */
__declspec(dllexport)
int TD_TakeShotRequested(void) { int r = g.shot_req; g.shot_req = 0; return r; }

/* ------------------------------------------------------------------------ */
/* Set the "Order" row text ("MARKET" or e.g. "BUY LIMIT @ 1.08200"). The EA */
/* computes this from the edited entry vs current market and calls it live.  */
__declspec(dllexport)
void TD_SetOrderType(const wchar_t *s)
{
    if (g.hOrder && s) SetWindowTextW(g.hOrder, s);
}

/* ------------------------------------------------------------------------ */
/* Fill the upcoming-events list. The EA passes BOTH forms (absolute + relative
   dates); the dialog shows one per the coach-mode toggle, so redaction needs no
   round-trip to MQL.                                                         */
__declspec(dllexport)
void TD_SetEvents(const wchar_t *abs_dates, const wchar_t *rel_dates)
{
    wcsncpy(g.evAbs, abs_dates ? abs_dates : L"", 4095); g.evAbs[4095] = 0;
    wcsncpy(g.evRel, rel_dates ? rel_dates : L"", 4095); g.evRel[4095] = 0;
    set_events_text();
}

/* ==========================================================================
 *  MANAGEMENT PANEL  (mid-trade actions; own thread so it survives a pause)
 * ========================================================================== */
static ATOM gm_cls = 0;
static const wchar_t *PCLS = L"HybridMgmtPanel";

/* panel layout metrics */
#define P_W       300
#define P_PAD     12
#define P_STATEH  178
#define P_BTNH    30
#define P_BTNGAP  8
#define P_BTNW    ((P_W - 2*P_PAD - 2*P_BTNGAP) / 3)

static void panel_disarm(void)
{
    gm.armed = 0;
    if (gm.hClose)   SetWindowTextW(gm.hClose,   L"Close");
    if (gm.hClose50) SetWindowTextW(gm.hClose50, L"Close 50%");
}

static LRESULT CALLBACK PanelProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CTLCOLORSTATIC: {           /* the state box: white read-only field */
        HDC hdc = (HDC)wp; HWND hc = (HWND)lp;
        if (GetDlgCtrlID(hc) == ID_MSTATE) {
            SetTextColor(hdc, COL_VALUE); SetBkColor(hdc, COL_EDITBG);
            return (LRESULT)(g_edbg ? g_edbg : (HBRUSH)GetStockObject(WHITE_BRUSH));
        }
        SetBkMode(hdc, TRANSPARENT); SetTextColor(hdc, COL_VALUE);
        return (LRESULT)(g_bg ? g_bg : (HBRUSH)GetStockObject(WHITE_BRUSH));
    }
    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wp;
        SetTextColor(hdc, COL_VALUE); SetBkColor(hdc, COL_EDITBG);
        return (LRESULT)(g_edbg ? g_edbg : (HBRUSH)GetStockObject(WHITE_BRUSH));
    }
    case WM_CTLCOLORBTN:
    case WM_CTLCOLORDLG:
        return (LRESULT)(g_bg ? g_bg : (HBRUSH)GetStockObject(WHITE_BRUSH));

    case WM_COMMAND: {
        int id = LOWORD(wp);
        if (id == ID_MBE) { InterlockedExchange(&gm.action, 3); return 0; }  /* instant */
        if (id == ID_MCLOSE) {
            if (gm.armed == 1) { InterlockedExchange(&gm.action, 1); panel_disarm(); }
            else { panel_disarm(); gm.armed = 1; SetWindowTextW(gm.hClose, L"Confirm CLOSE"); }
            return 0;
        }
        if (id == ID_MCLOSE50) {
            if (gm.armed == 2) { InterlockedExchange(&gm.action, 2); panel_disarm(); }
            else { panel_disarm(); gm.armed = 2; SetWindowTextW(gm.hClose50, L"Confirm 50%"); }
            return 0;
        }
        break;
    }

    case PM_UPDATE: {                    /* EA pushed fresh state (our thread now) */
        EnterCriticalSection(&gm.cs);
        SetWindowTextW(gm.hState, gm.buf);
        double lots = gm.lots;
        LeaveCriticalSection(&gm.cs);
        EnableWindow(gm.hBe, gm.be_enable ? TRUE : FALSE);
        double d = lots - gm.last_lots; if (d < 0) d = -d;
        if (gm.last_lots >= 0 && d > 1e-9) panel_disarm();   /* volume changed => stale confirm */
        gm.last_lots = lots;
        return 0;
    }
    case PM_KILL:
        DestroyWindow(hwnd); gm.hwnd = NULL; PostQuitMessage(0);
        return 0;
    case WM_CLOSE:                        /* [X]: minimise (recoverable from taskbar), never destroy */
        ShowWindow(hwnd, SW_MINIMIZE);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static DWORD WINAPI panel_thread(LPVOID param)
{
    (void)param;
    if (gm_cls == 0) {
        WNDCLASSEXW wc; ZeroMemory(&wc, sizeof(wc));
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = PanelProc;
        wc.hInstance = g_hinst;
        wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
        wc.hbrBackground = g_bg;
        wc.lpszClassName = PCLS;
        gm_cls = RegisterClassExW(&wc);
    }
    gm.fBody = make_font(-15, FW_NORMAL);
    gm.fBtn  = make_font(-15, FW_NORMAL);

    DWORD style   = WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    DWORD exstyle = WS_EX_APPWINDOW;               /* taskbar button => minimise/restore works */
    int clientH = P_PAD + P_STATEH + 10 + P_BTNH + P_PAD;
    RECT rc = { 0, 0, P_W, clientH };
    AdjustWindowRectEx(&rc, style, FALSE, exstyle);
    int winW = rc.right - rc.left, winH = rc.bottom - rc.top;
    int px = GetSystemMetrics(SM_CXSCREEN) - winW - 24;   /* anchor: top-right corner */
    int py = 64;

    HWND hwnd = CreateWindowExW(exstyle, PCLS,
        gm.title[0] ? gm.title : L"Manage position",
        style, px, py, winW, winH, NULL, NULL, g_hinst, NULL);
    if (!hwnd) { SetEvent(gm.hReady); return 0; }

    int y = P_PAD;
    gm.hState = CreateWindowExW(0, L"EDIT", L"",
        WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_READONLY | ES_LEFT,
        P_PAD, y, P_W - 2*P_PAD, P_STATEH, hwnd, (HMENU)(INT_PTR)ID_MSTATE, g_hinst, NULL);
    SendMessageW(gm.hState, WM_SETFONT, (WPARAM)gm.fBody, TRUE);
    y += P_STATEH + 10;

    gm.hClose = CreateWindowExW(0, L"BUTTON", L"Close",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        P_PAD, y, P_BTNW, P_BTNH, hwnd, (HMENU)(INT_PTR)ID_MCLOSE, g_hinst, NULL);
    gm.hClose50 = CreateWindowExW(0, L"BUTTON", L"Close 50%",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        P_PAD + P_BTNW + P_BTNGAP, y, P_BTNW, P_BTNH, hwnd, (HMENU)(INT_PTR)ID_MCLOSE50, g_hinst, NULL);
    gm.hBe = CreateWindowExW(0, L"BUTTON", L"SL → BE",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        P_PAD + 2*(P_BTNW + P_BTNGAP), y, P_BTNW, P_BTNH, hwnd, (HMENU)(INT_PTR)ID_MBE, g_hinst, NULL);
    SendMessageW(gm.hClose,   WM_SETFONT, (WPARAM)gm.fBtn, TRUE);
    SendMessageW(gm.hClose50, WM_SETFONT, (WPARAM)gm.fBtn, TRUE);
    SendMessageW(gm.hBe,      WM_SETFONT, (WPARAM)gm.fBtn, TRUE);

    gm.hwnd = hwnd;
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);      /* visible but don't steal focus */
    SetEvent(gm.hReady);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(gm.hwnd ? gm.hwnd : hwnd, &msg)) {
            TranslateMessage(&msg); DispatchMessageW(&msg);
        }
    }
    if (gm.fBody) { DeleteObject(gm.fBody); gm.fBody = NULL; }
    if (gm.fBtn)  { DeleteObject(gm.fBtn);  gm.fBtn  = NULL; }
    return 0;
}

/* Open the management panel (idempotent). Returns 1 if the window exists. */
__declspec(dllexport)
int TDM_Open(const wchar_t *title)
{
    if (gm.hThread && gm.hwnd) return 1;       /* already up */
    if (gm.hThread) {
        /* thread exists but no window yet: still starting, OR it died (CreateWindow
           failed / ready-wait timed out). A dead thread would wedge every retry at
           "return 0" forever, so reap it and fall through to a clean re-open.      */
        if (WaitForSingleObject(gm.hThread, 0) != WAIT_OBJECT_0) return 0;  /* genuinely starting */
        CloseHandle(gm.hThread); gm.hThread = NULL;
        if (gm.hReady) { CloseHandle(gm.hReady); gm.hReady = NULL; }
        if (gm.inited) { DeleteCriticalSection(&gm.cs); gm.inited = 0; }
    }
    ZeroMemory(&gm, sizeof(gm));
    gm.last_lots = -1.0;
    wcsncpy(gm.title, title ? title : L"Manage position", 127); gm.title[127] = 0;
    if (g_bg   == NULL) g_bg   = CreateSolidBrush(COL_BG);
    if (g_edbg == NULL) g_edbg = CreateSolidBrush(COL_EDITBG);
    InitializeCriticalSection(&gm.cs); gm.inited = 1;
    gm.hReady = CreateEventW(NULL, TRUE, FALSE, NULL);
    gm.hThread = CreateThread(NULL, 0, panel_thread, NULL, 0, NULL);
    if (!gm.hThread) {
        if (gm.hReady) { CloseHandle(gm.hReady); gm.hReady = NULL; }
        DeleteCriticalSection(&gm.cs); gm.inited = 0;
        return 0;
    }
    if (gm.hReady) WaitForSingleObject(gm.hReady, 3000);
    return gm.hwnd ? 1 : 0;
}

/* Push the live position-state text (multiline), current volume (for the arm-
   disarm on an auto scale-out), and whether SL->BE is currently valid.        */
__declspec(dllexport)
void TDM_Update(const wchar_t *state_text, double lots, int be_enabled)
{
    if (!gm.hwnd) return;
    EnterCriticalSection(&gm.cs);
    wcsncpy(gm.buf, state_text ? state_text : L"", 1023); gm.buf[1023] = 0;
    gm.lots = lots;
    LeaveCriticalSection(&gm.cs);
    InterlockedExchange(&gm.be_enable, be_enabled ? 1 : 0);
    PostMessageW(gm.hwnd, PM_UPDATE, 0, 0);
}

/* Drain a confirmed button action: 0 none, 1 close, 2 close50, 3 SL->BE. The EA
   executes the trade op itself (on the MQL thread) - this only reports intent. */
__declspec(dllexport)
int TDM_Poll(void) { return (int)InterlockedExchange(&gm.action, 0); }

/* Tear the panel down (position went flat / test ended). Joins the thread. */
__declspec(dllexport)
void TDM_Close(void)
{
    if (gm.hThread) {
        if (gm.hwnd) PostMessageW(gm.hwnd, PM_KILL, 0, 0);
        WaitForSingleObject(gm.hThread, 2000);
        CloseHandle(gm.hThread); gm.hThread = NULL;
    }
    if (gm.hReady) { CloseHandle(gm.hReady); gm.hReady = NULL; }
    if (gm.inited) { DeleteCriticalSection(&gm.cs); gm.inited = 0; }
    gm.hwnd = NULL;
}

/* ------------------------------------------------------------------------ */
/* Legacy modal export - retained for binary compatibility only; the EA now  */
/* drives the poll API above. Returns 0 (deny) as a safe no-op stub.         */
__declspec(dllexport)
int ShowTradeDialog(const wchar_t *title,  const wchar_t *symbol,
                    const wchar_t *strategy, const wchar_t *direction,
                    const wchar_t *entry,  const wchar_t *sl,
                    const wchar_t *tp,     const wchar_t *lots,
                    const wchar_t *rr)
{
    (void)title;(void)symbol;(void)strategy;(void)direction;
    (void)entry;(void)sl;(void)tp;(void)lots;(void)rr;
    return 0;
}
