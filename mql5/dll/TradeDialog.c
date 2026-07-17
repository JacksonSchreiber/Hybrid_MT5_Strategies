/*
 * TradeDialog.c  --  FTMO Hybrid Trading System
 *
 * A tiny Win32 helper DLL that renders a COLOUR-CODED, system-modal
 * approve/deny dialog for the Phase-2 interactive tester harness
 * (mql5/experts/HybridForwardTest.mq5). It replaces user32.dll's
 * MessageBoxW so the SL/entry/TP text can be coloured to match the chart
 * overlays (MessageBoxW cannot colour text).
 *
 * Export (single function):
 *   int ShowTradeDialog(const wchar_t* title,
 *                       const wchar_t* symbol,
 *                       const wchar_t* strategy,
 *                       const wchar_t* direction,   // "BUY" / "SELL"
 *                       const wchar_t* entry,
 *                       const wchar_t* sl,
 *                       const wchar_t* tp,
 *                       const wchar_t* lots,
 *                       const wchar_t* rr);
 *   returns 1 = Yes/Approve, 0 = No/Deny.
 *
 * Behaviour: creates a centred, top-most, caption+sysmenu popup with owner
 * NULL and runs its OWN modal message loop, so the calling (tester) thread
 * is blocked until the user answers -- exactly like MessageBoxW, which is
 * what freezes the visual tester. No other windows are owned. A static
 * re-entrancy guard makes a second concurrent call return 0 immediately.
 *
 * Colours are built with the RGB() macro (RGB() packs a COLORREF as
 * 0x00BBGGRR, so byte order is handled correctly -- red is red, not blue):
 *     entry     = RGB(0,160,0)    green   (chart clr* entry line)
 *     SL        = RGB(204,0,0)    red     (chart SL line)
 *     TP        = RGB(0,0,204)    blue    (chart TP line)
 *     BUY       = green, SELL     = red
 * These match the harness overlay palette documented in tester-harness.md.
 *
 * Build (from WSL, no Visual Studio / mingw needed):
 *   see mql5/dll/build.sh   (zig cc -target x86_64-windows-gnu)
 */

#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>

/* ---- colour palette (RGB macro => correct COLORREF byte order) ---------- */
#define COL_ENTRY   RGB(0,160,0)      /* green  */
#define COL_SL      RGB(204,0,0)      /* red    */
#define COL_TP      RGB(0,0,204)      /* blue   */
#define COL_BUY     RGB(0,160,0)      /* green  */
#define COL_SELL    RGB(204,0,0)      /* red    */
#define COL_LABEL   RGB(90,90,90)     /* field captions (grey) */
#define COL_VALUE   RGB(20,20,20)     /* neutral values (near-black) */
#define COL_BG      RGB(248,248,248)  /* dialog background */

/* ---- control ids -------------------------------------------------------- */
#define ID_YES      100
#define ID_NO       101
#define ID_FIRSTVAL 200   /* value statics get ids ID_FIRSTVAL + row index */

#define ROWS        8     /* symbol, strategy, direction, entry, sl, tp, lots, rr */

static HINSTANCE g_hinst    = NULL;
static LONG      g_inuse    = 0;      /* re-entrancy guard */
static ATOM      g_cls      = 0;
static HBRUSH    g_bg       = NULL;   /* process-global: outlives every call    */
                                      /* (the window class holds it; must not be */
                                      /* deleted per-call or the class dangles)  */
static const wchar_t *CLS   = L"HybridTradeDlg";

/* per-instance dialog state (lives on ShowTradeDialog's stack) */
typedef struct {
    const wchar_t *label[ROWS];   /* caption text  */
    const wchar_t *value[ROWS];   /* value text    */
    COLORREF       vcolor[ROWS];  /* value colour  */
    int            vbold[ROWS];   /* bold flag     */
    HFONT          fNormal;
    HFONT          fBold;
    HFONT          fCaption;
    HBRUSH         bg;
    HWND           hValue[ROWS];
    HWND           hLabel[ROWS];
    HWND           hYes;
    HWND           hNo;
    int            result;        /* -1 pending, 0 no, 1 yes */
} DlgState;

/* ------------------------------------------------------------------------ */
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_hinst = h;
        DisableThreadLibraryCalls(h);
    }
    return TRUE;
}

static HFONT make_font(int height, int weight)
{
    return CreateFontW(height, 0, 0, 0, weight, FALSE, FALSE, FALSE,
                       DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                       CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                       L"Segoe UI");
}

static void finish(HWND hwnd, int r)
{
    DlgState *st = (DlgState*)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
    if (st) st->result = r;
}

/* ------------------------------------------------------------------------ */
static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    DlgState *st = (DlgState*)GetWindowLongPtrW(hwnd, GWLP_USERDATA);

    switch (msg) {
    case WM_CTLCOLORSTATIC: {
        HDC hdc = (HDC)wp;
        HWND hctl = (HWND)lp;
        SetBkMode(hdc, TRANSPARENT);
        COLORREF c = COL_VALUE;
        if (st) {
            int id = GetDlgCtrlID(hctl);
            if (id >= ID_FIRSTVAL && id < ID_FIRSTVAL + ROWS)
                c = st->vcolor[id - ID_FIRSTVAL];
            else
                c = COL_LABEL;                 /* caption statics */
        }
        SetTextColor(hdc, c);
        return (LRESULT)(st ? st->bg : GetStockObject(WHITE_BRUSH));
    }
    case WM_CTLCOLORBTN:
    case WM_CTLCOLORDLG:
        return (LRESULT)(st ? st->bg : GetStockObject(WHITE_BRUSH));

    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_YES: finish(hwnd, 1); return 0;
        case ID_NO:  finish(hwnd, 0); return 0;
        }
        break;

    case WM_CLOSE:
        finish(hwnd, 0);                        /* [X] / Alt+F4 => deny */
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------------------ */
__declspec(dllexport)
int ShowTradeDialog(const wchar_t *title,  const wchar_t *symbol,
                    const wchar_t *strategy, const wchar_t *direction,
                    const wchar_t *entry,  const wchar_t *sl,
                    const wchar_t *tp,     const wchar_t *lots,
                    const wchar_t *rr)
{
    /* re-entrancy guard: never open a second dialog concurrently */
    if (InterlockedCompareExchange(&g_inuse, 1, 0) != 0) {
        OutputDebugStringW(L"[TradeDialog] concurrent call ignored -> deny\n");
        return 0;
    }

    DlgState st;
    ZeroMemory(&st, sizeof(st));
    st.result = -1;

    int isBuy = (direction && (direction[0] == L'B' || direction[0] == L'b'));

    /* rows: caption + value + colour + bold */
    const wchar_t *caps[ROWS] =
        { L"Symbol",  L"Strategy", L"Direction", L"Entry",
          L"Stop Loss", L"Take Profit", L"Lot size", L"R : R" };
    const wchar_t *vals[ROWS] =
        { symbol ? symbol : L"", strategy ? strategy : L"",
          direction ? direction : L"", entry ? entry : L"",
          sl ? sl : L"", tp ? tp : L"", lots ? lots : L"", rr ? rr : L"" };
    COLORREF cols[ROWS] =
        { COL_VALUE, COL_VALUE, isBuy ? COL_BUY : COL_SELL,
          COL_ENTRY, COL_SL, COL_TP, COL_VALUE, COL_VALUE };
    int bold[ROWS] = { 0,0,1,1,1,1,0,0 };

    for (int i = 0; i < ROWS; i++) {
        st.label[i]  = caps[i];
        st.value[i]  = vals[i];
        st.vcolor[i] = cols[i];
        st.vbold[i]  = bold[i];
    }

    /* fonts (per-call; controls are destroyed before these are deleted) */
    st.fNormal  = make_font(-18, FW_NORMAL);   /* ~13px Segoe UI */
    st.fBold    = make_font(-18, FW_BOLD);
    st.fCaption = make_font(-16, FW_NORMAL);

    /* background brush: created once, shared by every dialog (never deleted) */
    if (g_bg == NULL)
        g_bg = CreateSolidBrush(COL_BG);
    st.bg = g_bg;

    /* register class once */
    if (g_cls == 0) {
        WNDCLASSEXW wc;
        ZeroMemory(&wc, sizeof(wc));
        wc.cbSize        = sizeof(wc);
        wc.lpfnWndProc   = WndProc;
        wc.hInstance     = g_hinst;
        wc.hCursor       = LoadCursorW(NULL, IDC_ARROW);
        wc.hbrBackground = g_bg;
        wc.lpszClassName = CLS;
        g_cls = RegisterClassExW(&wc);
    }

    /* --- layout geometry --- */
    const int PADX = 22, PADY = 18;
    const int LABELW = 108;          /* caption column width */
    const int VALUEW = 250;          /* value column width   */
    const int ROWH  = 30;
    const int CLIENTW = PADX + LABELW + 10 + VALUEW + PADX;
    const int rowsTop = PADY;
    const int rowsBot = rowsTop + ROWS * ROWH;
    const int BTNW = 110, BTNH = 34, BTNGAP = 20;
    const int btnTop = rowsBot + 16;
    const int CLIENTH = btnTop + BTNH + PADY;

    RECT rc = { 0, 0, CLIENTW, CLIENTH };
    DWORD style   = WS_POPUP | WS_CAPTION | WS_SYSMENU;
    DWORD exstyle = WS_EX_TOPMOST | WS_EX_DLGMODALFRAME;
    AdjustWindowRectEx(&rc, style, FALSE, exstyle);
    int winW = rc.right - rc.left;
    int winH = rc.bottom - rc.top;
    int scrW = GetSystemMetrics(SM_CXSCREEN);
    int scrH = GetSystemMetrics(SM_CYSCREEN);
    int x = (scrW - winW) / 2;
    int y = (scrH - winH) / 2;

    HWND hwnd = CreateWindowExW(exstyle, CLS, title ? title : L"Trade Signal",
                                style, x, y, winW, winH,
                                NULL, NULL, g_hinst, NULL);
    if (!hwnd) {
        DeleteObject(st.fNormal); DeleteObject(st.fBold); DeleteObject(st.fCaption);
        InterlockedExchange(&g_inuse, 0);
        return 0;                    /* fail closed => deny */
    }
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, (LONG_PTR)&st);

    /* --- child controls --- */
    for (int i = 0; i < ROWS; i++) {
        int ry = rowsTop + i * ROWH;
        st.hLabel[i] = CreateWindowExW(0, L"STATIC", st.label[i],
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            PADX, ry, LABELW, ROWH - 6, hwnd, NULL, g_hinst, NULL);
        SendMessageW(st.hLabel[i], WM_SETFONT, (WPARAM)st.fCaption, TRUE);

        st.hValue[i] = CreateWindowExW(0, L"STATIC", st.value[i],
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            PADX + LABELW + 10, ry, VALUEW, ROWH - 6,
            hwnd, (HMENU)(INT_PTR)(ID_FIRSTVAL + i), g_hinst, NULL);
        SendMessageW(st.hValue[i], WM_SETFONT,
                     (WPARAM)(st.vbold[i] ? st.fBold : st.fNormal), TRUE);
    }

    int bx = (CLIENTW - (BTNW * 2 + BTNGAP)) / 2;
    st.hYes = CreateWindowExW(0, L"BUTTON", L"&Yes  (approve)",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        bx, btnTop, BTNW, BTNH, hwnd, (HMENU)(INT_PTR)ID_YES, g_hinst, NULL);
    st.hNo = CreateWindowExW(0, L"BUTTON", L"&No  (skip)",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
        bx + BTNW + BTNGAP, btnTop, BTNW, BTNH,
        hwnd, (HMENU)(INT_PTR)ID_NO, g_hinst, NULL);
    SendMessageW(st.hYes, WM_SETFONT, (WPARAM)st.fNormal, TRUE);
    SendMessageW(st.hNo,  WM_SETFONT, (WPARAM)st.fNormal, TRUE);

    ShowWindow(hwnd, SW_SHOW);
    SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
    SetForegroundWindow(hwnd);
    SetFocus(st.hNo);                 /* default = No (safety) */

    /* --- modal message loop (blocks the calling thread) --- */
    MSG msg;
    while (st.result < 0 && GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (msg.message == WM_KEYDOWN) {
            if (msg.wParam == 'Y') { st.result = 1; break; }
            if (msg.wParam == 'N' || msg.wParam == VK_ESCAPE) { st.result = 0; break; }
            if (msg.wParam == VK_RETURN) {           /* Enter = focused button */
                st.result = (GetFocus() == st.hYes) ? 1 : 0;
                break;
            }
        }
        if (!IsDialogMessageW(hwnd, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    int result = (st.result == 1) ? 1 : 0;

    DestroyWindow(hwnd);
    DeleteObject(st.fNormal);
    DeleteObject(st.fBold);
    DeleteObject(st.fCaption);
    InterlockedExchange(&g_inuse, 0);
    return result;
}
