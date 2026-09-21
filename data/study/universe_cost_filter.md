# Universe cost filter — spread drag over the FTMO list (coach 2026-09-21)

Run 2026-09-21 16:07 UTC on the shadow box (OANDA-Demo-1) via `pipeline/universe_cost_filter.py`. Rule: attach iff round-trip spread drag ≤ **0.10R**/trade; USDCNH excluded regardless. Stop = 1.385 × median H4 ATR(14) over the last 360 closed bars (the §10.2 lineup-median detector stop, in ATR units); spread = median of 12 live samples 2.5 s apart. Commission and swaps are not in the drag (same as the original table).

| # | symbol | verdict | drag (R) | drag at a 1.0-ATR stop | median spread | max spread seen | est. stop | H4 ATR(14) | price | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | US100.sim | **ATTACH** | 0.0029 | 0.00401 | 0.8 | 1.3 | 276.08 | 199.33 | 29968.3 |  |
| 2 | BTCUSD.sim | **ATTACH** | 0.00334 | 0.00462 | 4 | 28 | 1198.1 | 865.04 | 85285 |  |
| 3 | USDCAD.sim | **ATTACH** | 0.00692 | 0.00958 | 2e-05 | 3e-05 | 0.0028912 | 0.0020875 | 1.39966 |  |
| 4 | XAGUSD.sim | **ATTACH** | 0.00772 | 0.0107 | 0.0105 | 0.017 | 1.3594 | 0.98149 | 66.752 |  |
| 5 | EURUSD.sim | **ATTACH** | 0.00789 | 0.0109 | 2e-05 | 3e-05 | 0.0025347 | 0.0018301 | 1.1486 |  |
| 6 | US30.sim | **ATTACH** | 0.00859 | 0.0119 | 2.5 | 3.5 | 291.1 | 210.18 | 52147.1 |  |
| 7 | AUDUSD.sim | **ATTACH** | 0.00873 | 0.0121 | 2e-05 | 3e-05 | 0.0022916 | 0.0016546 | 0.71356 |  |
| 8 | XAUUSD.sim | **ATTACH** | 0.00906 | 0.0126 | 0.465 | 0.62 | 51.313 | 37.049 | 4359.36 |  |
| 9 | US500.sim | **ATTACH** | 0.00987 | 0.0137 | 0.4 | 0.6 | 40.541 | 29.272 | 7704.9 |  |
| 10 | USDJPY.sim | **ATTACH** | 0.0106 | 0.0147 | 0.005 | 0.008 | 0.47013 | 0.33944 | 157.333 |  |
| 11 | USDCHF.sim | **ATTACH** | 0.0111 | 0.0153 | 3e-05 | 4e-05 | 0.0027102 | 0.0019569 | 0.8215 |  |
| 12 | GBPUSD.sim | **ATTACH** | 0.0116 | 0.016 | 4e-05 | 6e-05 | 0.0034532 | 0.0024933 | 1.33909 |  |
| 13 | NZDUSD.sim | **ATTACH** | 0.0127 | 0.0176 | 3e-05 | 4e-05 | 0.002367 | 0.001709 | 0.57307 |  |
| 14 | USDMXN.sim | **ATTACH** | 0.0128 | 0.0178 | 0.00065 | 0.00099 | 0.050715 | 0.036618 | 17.1901 |  |
| 15 | USDZAR.sim | **ATTACH** | 0.0156 | 0.0216 | 0.00131 | 0.00246 | 0.084115 | 0.060733 | 16.2608 |  |
| 16 | USOIL.sim | **ATTACH** | 0.0156 | 0.0216 | 0.03 | 0.041 | 1.9229 | 1.3884 | 97.656 |  |
| 17 | AUDJPY.sim | **ATTACH** | 0.0159 | 0.022 | 0.006 | 0.007 | 0.37704 | 0.27223 | 112.266 |  |
| 18 | USDCNH.sim | **EXCLUDED** | 0.0165 | 0.0228 | 9e-05 | 0.00011 | 0.0054569 | 0.00394 | 6.69222 | managed price (coach ruling) - excluded regardless of cost |
| 19 | EURJPY.sim | **ATTACH** | 0.0167 | 0.0231 | 0.0075 | 0.009 | 0.44993 | 0.32486 | 180.728 |  |
| 20 | EURCAD.sim | **ATTACH** | 0.0192 | 0.0265 | 6e-05 | 8e-05 | 0.0031318 | 0.0022612 | 1.60758 |  |
| 21 | CADJPY.sim | **ATTACH** | 0.0209 | 0.0289 | 0.007 | 0.007 | 0.33566 | 0.24235 | 112.422 |  |
| 22 | USDSGD.sim | **ATTACH** | 0.0218 | 0.0302 | 5e-05 | 7e-05 | 0.0022968 | 0.0016584 | 1.27495 |  |
| 23 | GBPJPY.sim | **ATTACH** | 0.0218 | 0.0302 | 0.013 | 0.014 | 0.59647 | 0.43067 | 210.69 |  |
| 24 | EURAUD.sim | **ATTACH** | 0.022 | 0.0304 | 8.5e-05 | 0.0001 | 0.0038719 | 0.0027956 | 1.60964 |  |
| 25 | EURGBP.sim | **ATTACH** | 0.022 | 0.0305 | 3e-05 | 4e-05 | 0.0013623 | 0.00098364 | 0.85772 |  |
| 26 | AUDCAD.sim | **ATTACH** | 0.0223 | 0.0309 | 6e-05 | 7e-05 | 0.0026887 | 0.0019413 | 0.99866 |  |
| 27 | EURCHF.sim | **ATTACH** | 0.0226 | 0.0313 | 4e-05 | 5e-05 | 0.0017702 | 0.0012781 | 0.94359 |  |
| 28 | AUDNZD.sim | **ATTACH** | 0.023 | 0.0318 | 7e-05 | 0.0001 | 0.0030493 | 0.0022016 | 1.24509 |  |
| 29 | NZDJPY.sim | **ATTACH** | 0.0233 | 0.0322 | 0.008 | 0.009 | 0.34362 | 0.2481 | 90.167 |  |
| 30 | CADCHF.sim | **ATTACH** | 0.0234 | 0.0325 | 4e-05 | 5e-05 | 0.0017062 | 0.0012319 | 0.58695 |  |
| 31 | NZDCAD.sim | **ATTACH** | 0.0255 | 0.0354 | 7e-05 | 8e-05 | 0.0027399 | 0.0019783 | 0.80206 |  |
| 32 | GBPAUD.sim | **ATTACH** | 0.0259 | 0.0358 | 0.00013 | 0.00016 | 0.0050271 | 0.0036297 | 1.8766 |  |
| 33 | USDSEK.sim | **ATTACH** | 0.0272 | 0.0377 | 0.00115 | 0.00147 | 0.04221 | 0.030476 | 9.8214 |  |
| 34 | AUDCHF.sim | **ATTACH** | 0.0288 | 0.0399 | 5e-05 | 6e-05 | 0.0017366 | 0.0012539 | 0.58618 |  |
| 35 | GBPCAD.sim | **ATTACH** | 0.0294 | 0.0407 | 0.000125 | 0.00014 | 0.0042585 | 0.0030748 | 1.87415 |  |
| 36 | CHFJPY.sim | **ATTACH** | 0.0312 | 0.0432 | 0.018 | 0.02 | 0.57672 | 0.41641 | 191.553 |  |
| 37 | NZDCHF.sim | **ATTACH** | 0.0314 | 0.0434 | 5e-05 | 6e-05 | 0.0015948 | 0.0011515 | 0.47078 |  |
| 38 | EURSEK.sim | **ATTACH** | 0.0338 | 0.0468 | 0.001125 | 0.00135 | 0.033295 | 0.02404 | 11.2808 |  |
| 39 | GBPCHF.sim | **ATTACH** | 0.0374 | 0.0518 | 0.000105 | 0.00012 | 0.002807 | 0.0020267 | 1.10006 |  |
| 40 | EURNZD.sim | **ATTACH** | 0.0419 | 0.0581 | 0.000245 | 0.00025 | 0.0058411 | 0.0042174 | 2.00408 |  |
| 41 | EURNOK.sim | **ATTACH** | 0.0426 | 0.059 | 0.0017 | 0.0018 | 0.039897 | 0.028806 | 10.8111 |  |
| 42 | USDNOK.sim | **ATTACH** | 0.0449 | 0.0621 | 0.001835 | 0.00193 | 0.040912 | 0.02954 | 9.4124 |  |
| 43 | GBPNZD.sim | **ATTACH** | 0.0494 | 0.0684 | 0.00035 | 0.00036 | 0.0070834 | 0.0051143 | 2.33643 |  |
| 44 | USDHUF.sim | **ATTACH** | 0.0498 | 0.0689 | 0.0965 | 0.115 | 1.9397 | 1.4005 | 315.473 |  |
| 45 | USDPLN.sim | **ATTACH** | 0.0539 | 0.0746 | 0.000685 | 0.00097 | 0.012715 | 0.0091802 | 3.78855 |  |
| 46 | EURHUF.sim | **ATTACH** | 0.0564 | 0.0781 | 0.099 | 0.136 | 1.7561 | 1.2679 | 362.365 |  |
| 47 | USDCZK.sim | **ATTACH** | 0.0595 | 0.0824 | 0.0036 | 0.0037 | 0.060538 | 0.04371 | 21.1967 |  |
| 48 | EURPLN.sim | **ATTACH** | 0.0776 | 0.108 | 0.00065 | 0.00084 | 0.0083718 | 0.0060446 | 4.35162 |  |
| 49 | EURCZK.sim | **EXCLUDED** | 0.113 | 0.157 | 0.0037 | 0.0037 | 0.032699 | 0.023609 | 24.3476 | drag 0.113R > 0.10R |

**Attach (47):** US100.sim, BTCUSD.sim, USDCAD.sim, XAGUSD.sim, EURUSD.sim, US30.sim, AUDUSD.sim, XAUUSD.sim, US500.sim, USDJPY.sim, USDCHF.sim, GBPUSD.sim, NZDUSD.sim, USDMXN.sim, USDZAR.sim, USOIL.sim, AUDJPY.sim, EURJPY.sim, EURCAD.sim, CADJPY.sim, USDSGD.sim, GBPJPY.sim, EURAUD.sim, EURGBP.sim, AUDCAD.sim, EURCHF.sim, AUDNZD.sim, NZDJPY.sim, CADCHF.sim, NZDCAD.sim, GBPAUD.sim, USDSEK.sim, AUDCHF.sim, GBPCAD.sim, CHFJPY.sim, NZDCHF.sim, EURSEK.sim, GBPCHF.sim, EURNZD.sim, EURNOK.sim, USDNOK.sim, GBPNZD.sim, USDHUF.sim, USDPLN.sim, EURHUF.sim, USDCZK.sim, EURPLN.sim

**Excluded (2):** USDCNH.sim (managed price (coach ruling) - excluded regardless of cost), EURCZK.sim (drag 0.113R > 0.10R)
