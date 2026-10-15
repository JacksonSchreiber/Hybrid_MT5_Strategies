# Wait 4 h, enter on a positive average (up to 24 h) - trader 2026-10-15

5954 signals (open symbols; locked never loaded; 2025-26 bloc only), 25s. Raw self-check vs the rule engine's raw replay: max |diff| 0.00e+00R.

Every line counts every signal (a skip = 0R), paired with raw on the same signals. R at the 1% rule (a later fill re-based to its own stop distance). Model spread. The EA's R:R floor (TP2 >= 1.0R from the fill) applied unless said.

## All open signals (5954 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 5954 (100%) | -0.038 | -226.8 | +0.000 | +0.00 | 313.7 |
| enter at 4 h always | 5214 (88%) | -0.050 | -295.1 | -0.011 | -0.91 | 377.6 |
| 4 h check only (else skip) | 2750 (46%) | -0.011 | -65.5 | +0.027 | +1.54 | 170.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 3696 (62%) | -0.020 | -119.0 | +0.018 | +1.28 | 236.0 |
| the rule, checked at H4 closes only | 3619 (61%) | -0.011 | -66.2 | +0.027 | +1.81 | 191.9 |
| the rule on the 12 h pill's average | 3645 (61%) | -0.018 | -105.2 | +0.020 | +1.38 | 189.0 |

### Same, +1 pip round trip

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 5954 (100%) | -0.070 | -413.9 | +0.000 | +0.00 | 456.1 |
| enter at 4 h always | 5214 (88%) | -0.080 | -477.5 | -0.011 | -0.85 | 523.0 |
| 4 h check only (else skip) | 2750 (46%) | -0.023 | -135.6 | +0.047 | +2.65 | 225.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 3696 (62%) | -0.035 | -207.6 | +0.035 | +2.43 | 303.9 |
| the rule, checked at H4 closes only | 3619 (61%) | -0.026 | -153.2 | +0.044 | +2.92 | 258.7 |
| the rule on the 12 h pill's average | 3645 (61%) | -0.033 | -199.4 | +0.036 | +2.43 | 262.4 |

### Same, without the EA's R:R floor

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 5954 (100%) | -0.038 | -226.8 | +0.000 | +0.00 | 313.7 |
| enter at 4 h always | 5367 (90%) | -0.047 | -281.7 | -0.009 | -0.76 | 363.0 |
| 4 h check only (else skip) | 2899 (49%) | -0.009 | -51.5 | +0.029 | +1.70 | 154.8 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 3903 (66%) | -0.017 | -102.0 | +0.021 | +1.54 | 220.5 |
| the rule, checked at H4 closes only | 3839 (64%) | -0.008 | -47.7 | +0.030 | +2.10 | 170.7 |
| the rule on the 12 h pill's average | 3890 (65%) | -0.017 | -102.7 | +0.021 | +1.47 | 182.8 |

### Same, NOT re-based (same lots as raw - more than 1% at risk when the fill is above the signal)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 5954 (100%) | -0.038 | -226.8 | +0.000 | +0.00 | 313.7 |
| enter at 4 h always | 5214 (88%) | -0.042 | -251.5 | -0.004 | -0.48 | 354.4 |
| 4 h check only (else skip) | 2750 (46%) | -0.017 | -100.3 | +0.021 | +1.26 | 216.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 3696 (62%) | -0.029 | -174.6 | +0.009 | +0.69 | 305.5 |
| the rule, checked at H4 closes only | 3619 (61%) | -0.024 | -143.6 | +0.014 | +1.07 | 298.9 |
| the rule on the 12 h pill's average | 3645 (61%) | -0.028 | -164.2 | +0.011 | +0.80 | 262.5 |

## What the rule did

- entered 3696 of 5954: 2750 at the 4 h check, 946 later (median 3.3 trading h after the 4 h check).
- skipped 2258: never positive 1850, R:R floor refused 207, stop/TP2 before entry 201.
- the skipped signals' raw R: -0.534 / signal, total -1206.8R (what skipping them saved or cost).
- the entered signals: raw +0.265 / signal (total +980.0R) vs entered late -0.032 (total -119.0R); median fill +0.25R from the signal price (the stop is then 1.25x as far).
- exits of the entered: tp2 995, stop 2694, end 7 | the same signals raw: tp2 995, stop 2694, end 7

## Against skipping the same share at random

Expected R / signal of taking the same share of signals at random = raw's mean x the share the rule takes.

| group | signals | raw | the rule | random skip, same share | rule - random | same, +1 pip |
|---|---:|---:|---:|---:|---:|---:|
| all | 5954 | -0.038 | -0.020 | -0.024 | +0.004 | +0.008 |
| Commodity bloc | 3468 | -0.103 | -0.053 | -0.064 | +0.010 | +0.015 |
| Precious metals | 166 | +0.193 | +0.102 | +0.129 | -0.027 | -0.026 |
| Standalone | 504 | +0.000 | -0.042 | +0.000 | -0.043 | -0.041 |
| US indices | 822 | +0.102 | +0.054 | +0.066 | -0.012 | -0.005 |
| USD majors | 832 | +0.010 | +0.015 | +0.006 | +0.009 | +0.013 |
| Yen | 162 | +0.033 | +0.083 | +0.022 | +0.062 | +0.066 |

## Commodity bloc (3468 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 3468 (100%) | -0.103 | -356.5 | +0.000 | +0.00 | 402.6 |
| enter at 4 h always | 3053 (88%) | -0.124 | -430.4 | -0.021 | -1.32 | 489.2 |
| 4 h check only (else skip) | 1607 (46%) | -0.040 | -139.8 | +0.062 | +2.79 | 205.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 2144 (62%) | -0.053 | -184.8 | +0.050 | +2.75 | 270.2 |
| the rule, checked at H4 closes only | 2103 (61%) | -0.049 | -170.2 | +0.054 | +2.91 | 258.6 |
| the rule on the 12 h pill's average | 2138 (62%) | -0.056 | -193.4 | +0.047 | +2.50 | 254.2 |

## Precious metals (166 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 166 (100%) | +0.193 | +32.0 | +0.000 | +0.00 | 23.8 |
| enter at 4 h always | 151 (91%) | +0.180 | +29.9 | -0.013 | -0.22 | 22.8 |
| 4 h check only (else skip) | 80 (48%) | +0.066 | +10.9 | -0.127 | -1.14 | 14.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 111 (67%) | +0.102 | +16.9 | -0.091 | -1.08 | 12.3 |
| the rule, checked at H4 closes only | 111 (67%) | +0.090 | +15.0 | -0.102 | -1.19 | 12.1 |
| the rule on the 12 h pill's average | 111 (67%) | +0.128 | +21.3 | -0.064 | -0.73 | 12.8 |

## Standalone (504 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 504 (100%) | +0.000 | +0.0 | +0.000 | +0.00 | 50.6 |
| enter at 4 h always | 422 (84%) | -0.019 | -9.4 | -0.019 | -0.43 | 46.0 |
| 4 h check only (else skip) | 235 (47%) | -0.038 | -19.2 | -0.038 | -0.60 | 28.1 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 315 (62%) | -0.042 | -21.4 | -0.043 | -0.82 | 32.8 |
| the rule, checked at H4 closes only | 304 (60%) | +0.025 | +12.7 | +0.025 | +0.37 | 26.9 |
| the rule on the 12 h pill's average | 309 (61%) | -0.031 | -15.5 | -0.031 | -0.58 | 36.8 |

## US indices (822 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 822 (100%) | +0.102 | +84.0 | +0.000 | +0.00 | 63.2 |
| enter at 4 h always | 725 (88%) | +0.102 | +84.2 | +0.000 | +0.01 | 35.4 |
| 4 h check only (else skip) | 387 (47%) | +0.057 | +47.2 | -0.045 | -0.90 | 44.2 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 528 (64%) | +0.054 | +44.1 | -0.048 | -1.24 | 35.2 |
| the rule, checked at H4 closes only | 512 (62%) | +0.067 | +54.7 | -0.036 | -0.89 | 32.2 |
| the rule on the 12 h pill's average | 487 (59%) | +0.090 | +73.6 | -0.013 | -0.30 | 24.3 |

## USD majors (832 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 832 (100%) | +0.010 | +8.4 | +0.000 | +0.00 | 63.5 |
| enter at 4 h always | 716 (86%) | +0.022 | +18.3 | +0.012 | +0.36 | 55.1 |
| 4 h check only (else skip) | 369 (44%) | +0.023 | +18.9 | +0.013 | +0.26 | 56.1 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 492 (59%) | +0.015 | +12.7 | +0.005 | +0.13 | 46.3 |
| the rule, checked at H4 closes only | 486 (58%) | +0.008 | +6.9 | -0.002 | -0.04 | 50.2 |
| the rule on the 12 h pill's average | 496 (60%) | +0.001 | +1.2 | -0.009 | -0.21 | 41.1 |

## Yen (162 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 162 (100%) | +0.033 | +5.4 | +0.000 | +0.00 | 23.1 |
| enter at 4 h always | 147 (91%) | +0.076 | +12.4 | +0.043 | +0.40 | 19.5 |
| 4 h check only (else skip) | 72 (44%) | +0.101 | +16.4 | +0.068 | +0.64 | 9.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 106 (65%) | +0.083 | +13.5 | +0.050 | +0.64 | 12.0 |
| the rule, checked at H4 closes only | 103 (64%) | +0.091 | +14.7 | +0.058 | +0.72 | 10.0 |
| the rule on the 12 h pill's average | 104 (64%) | +0.047 | +7.7 | +0.014 | +0.17 | 10.6 |

## Detector DeepFib (271 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 271 (100%) | -0.021 | -5.6 | +0.000 | +0.00 | 47.4 |
| enter at 4 h always | 201 (74%) | +0.029 | +7.9 | +0.050 | +0.60 | 32.7 |
| 4 h check only (else skip) | 133 (49%) | +0.010 | +2.7 | +0.031 | +0.35 | 18.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 164 (61%) | -0.018 | -4.9 | +0.003 | +0.04 | 25.1 |
| the rule, checked at H4 closes only | 162 (60%) | +0.077 | +20.7 | +0.097 | +0.89 | 22.9 |
| the rule on the 12 h pill's average | 155 (57%) | +0.028 | +7.6 | +0.049 | +0.61 | 24.4 |

## Detector EMArevQ (267 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 267 (100%) | +0.097 | +25.8 | +0.000 | +0.00 | 17.7 |
| enter at 4 h always | 196 (73%) | +0.049 | +13.2 | -0.047 | -0.63 | 23.6 |
| 4 h check only (else skip) | 103 (39%) | +0.052 | +13.9 | -0.044 | -0.65 | 12.4 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 119 (45%) | +0.035 | +9.4 | -0.062 | -0.95 | 16.4 |
| the rule, checked at H4 closes only | 118 (44%) | +0.039 | +10.5 | -0.057 | -0.88 | 16.4 |
| the rule on the 12 h pill's average | 80 (30%) | +0.038 | +10.1 | -0.059 | -0.80 | 10.8 |

## Detector SweepMSS (1204 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 1204 (100%) | -0.025 | -30.6 | +0.000 | +0.00 | 66.4 |
| enter at 4 h always | 1061 (88%) | -0.034 | -40.5 | -0.008 | -0.35 | 70.5 |
| 4 h check only (else skip) | 531 (44%) | +0.014 | +16.3 | +0.039 | +1.08 | 25.5 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 718 (60%) | +0.021 | +24.8 | +0.046 | +1.56 | 30.4 |
| the rule, checked at H4 closes only | 703 (58%) | +0.009 | +10.6 | +0.034 | +1.12 | 34.6 |
| the rule on the 12 h pill's average | 921 (76%) | -0.010 | -11.7 | +0.016 | +0.62 | 40.3 |

## Detector TrendCont (4212 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 4212 (100%) | -0.051 | -216.4 | +0.000 | +0.00 | 348.5 |
| enter at 4 h always | 3756 (89%) | -0.065 | -275.8 | -0.014 | -0.95 | 394.0 |
| 4 h check only (else skip) | 1983 (47%) | -0.023 | -98.5 | +0.028 | +1.30 | 207.8 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 2695 (64%) | -0.035 | -148.4 | +0.016 | +0.95 | 256.2 |
| the rule, checked at H4 closes only | 2636 (63%) | -0.026 | -108.0 | +0.026 | +1.48 | 223.7 |
| the rule on the 12 h pill's average | 2489 (59%) | -0.026 | -111.2 | +0.025 | +1.36 | 196.8 |

## BUY (3273 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 3273 (100%) | -0.035 | -116.2 | +0.000 | +0.00 | 130.0 |
| enter at 4 h always | 2876 (88%) | -0.044 | -143.3 | -0.008 | -0.52 | 150.1 |
| 4 h check only (else skip) | 1531 (47%) | +0.009 | +30.0 | +0.045 | +1.90 | 52.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 2073 (63%) | -0.007 | -21.4 | +0.029 | +1.55 | 70.8 |
| the rule, checked at H4 closes only | 2031 (62%) | +0.008 | +25.0 | +0.043 | +2.14 | 70.5 |
| the rule on the 12 h pill's average | 2044 (62%) | -0.010 | -34.2 | +0.025 | +1.29 | 77.6 |

## SELL (2681 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 2681 (100%) | -0.041 | -110.6 | +0.000 | +0.00 | 236.8 |
| enter at 4 h always | 2338 (87%) | -0.057 | -151.9 | -0.015 | -0.76 | 281.2 |
| 4 h check only (else skip) | 1219 (45%) | -0.036 | -95.5 | +0.006 | +0.21 | 149.0 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 1623 (61%) | -0.036 | -97.7 | +0.005 | +0.22 | 185.1 |
| the rule, checked at H4 closes only | 1588 (59%) | -0.034 | -91.1 | +0.007 | +0.33 | 180.2 |
| the rule on the 12 h pill's average | 1601 (60%) | -0.026 | -71.0 | +0.015 | +0.65 | 155.8 |

## 2012-2018 (2562 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 2562 (100%) | -0.004 | -11.2 | +0.000 | +0.00 | 126.0 |
| enter at 4 h always | 2234 (87%) | -0.005 | -11.7 | -0.000 | -0.01 | 125.6 |
| 4 h check only (else skip) | 1166 (46%) | +0.025 | +64.8 | +0.030 | +1.10 | 63.7 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 1577 (62%) | +0.019 | +48.5 | +0.023 | +1.08 | 92.9 |
| the rule, checked at H4 closes only | 1534 (60%) | +0.024 | +60.6 | +0.028 | +1.26 | 86.7 |
| the rule on the 12 h pill's average | 1573 (61%) | +0.006 | +16.2 | +0.011 | +0.47 | 83.7 |

## 2019-2024 (3039 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 3039 (100%) | -0.046 | -140.4 | +0.000 | +0.00 | 171.9 |
| enter at 4 h always | 2679 (88%) | -0.065 | -196.3 | -0.018 | -1.10 | 214.9 |
| 4 h check only (else skip) | 1411 (46%) | -0.026 | -78.5 | +0.020 | +0.82 | 84.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 1888 (62%) | -0.035 | -107.4 | +0.011 | +0.54 | 119.3 |
| the rule, checked at H4 closes only | 1860 (61%) | -0.023 | -70.0 | +0.023 | +1.08 | 107.1 |
| the rule on the 12 h pill's average | 1847 (61%) | -0.027 | -83.1 | +0.019 | +0.91 | 97.0 |

## 2025-2026 (commodity bloc only - the rest is research-locked) (353 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 353 (100%) | -0.213 | -75.2 | +0.000 | +0.00 | 82.4 |
| enter at 4 h always | 301 (85%) | -0.247 | -87.2 | -0.034 | -0.80 | 96.7 |
| 4 h check only (else skip) | 173 (49%) | -0.147 | -51.9 | +0.066 | +0.97 | 51.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 231 (65%) | -0.170 | -60.1 | +0.043 | +0.81 | 65.8 |
| the rule, checked at H4 closes only | 225 (64%) | -0.161 | -56.8 | +0.052 | +0.97 | 62.7 |
| the rule on the 12 h pill's average | 225 (64%) | -0.108 | -38.3 | +0.105 | +2.01 | 48.7 |

## AUDCAD (588 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 588 (100%) | -0.090 | -52.7 | +0.000 | +0.00 | 73.2 |
| enter at 4 h always | 518 (88%) | -0.124 | -72.6 | -0.034 | -0.98 | 91.2 |
| 4 h check only (else skip) | 280 (48%) | -0.002 | -1.3 | +0.087 | +1.59 | 26.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 373 (63%) | +0.002 | +0.9 | +0.091 | +2.15 | 29.8 |
| the rule, checked at H4 closes only | 368 (63%) | -0.005 | -3.1 | +0.084 | +1.94 | 30.3 |
| the rule on the 12 h pill's average | 370 (63%) | -0.052 | -30.8 | +0.037 | +0.81 | 55.3 |

## AUDNZD (596 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 596 (100%) | -0.045 | -26.8 | +0.000 | +0.00 | 89.4 |
| enter at 4 h always | 530 (89%) | -0.035 | -20.9 | +0.010 | +0.18 | 91.3 |
| 4 h check only (else skip) | 279 (47%) | -0.021 | -12.7 | +0.024 | +0.42 | 46.7 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 372 (62%) | -0.023 | -13.4 | +0.022 | +0.50 | 56.3 |
| the rule, checked at H4 closes only | 363 (61%) | -0.017 | -10.3 | +0.028 | +0.60 | 56.5 |
| the rule on the 12 h pill's average | 371 (62%) | -0.007 | -4.3 | +0.038 | +0.84 | 47.3 |

## AUDUSD (551 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 551 (100%) | -0.014 | -7.6 | +0.000 | +0.00 | 47.1 |
| enter at 4 h always | 487 (88%) | -0.045 | -24.8 | -0.031 | -0.78 | 43.5 |
| 4 h check only (else skip) | 262 (48%) | -0.063 | -34.5 | -0.049 | -0.82 | 42.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 352 (64%) | -0.054 | -29.6 | -0.040 | -0.84 | 50.4 |
| the rule, checked at H4 closes only | 346 (63%) | -0.053 | -29.1 | -0.039 | -0.81 | 52.2 |
| the rule on the 12 h pill's average | 349 (63%) | -0.025 | -13.8 | -0.011 | -0.22 | 43.3 |

## EURUSD (360 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 360 (100%) | +0.014 | +4.9 | +0.000 | +0.00 | 53.6 |
| enter at 4 h always | 315 (88%) | -0.030 | -10.9 | -0.044 | -0.81 | 59.2 |
| 4 h check only (else skip) | 174 (48%) | +0.005 | +1.9 | -0.008 | -0.11 | 35.1 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 211 (59%) | +0.003 | +1.0 | -0.011 | -0.17 | 37.1 |
| the rule, checked at H4 closes only | 210 (58%) | +0.016 | +5.7 | +0.002 | +0.04 | 36.8 |
| the rule on the 12 h pill's average | 220 (61%) | +0.035 | +12.5 | +0.021 | +0.34 | 33.2 |

## GBPUSD (472 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 472 (100%) | +0.007 | +3.5 | +0.000 | +0.00 | 40.1 |
| enter at 4 h always | 401 (85%) | +0.062 | +29.2 | +0.054 | +1.30 | 30.0 |
| 4 h check only (else skip) | 195 (41%) | +0.036 | +17.0 | +0.029 | +0.43 | 31.2 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 281 (60%) | +0.025 | +11.7 | +0.017 | +0.33 | 27.1 |
| the rule, checked at H4 closes only | 276 (58%) | +0.003 | +1.2 | -0.005 | -0.09 | 30.1 |
| the rule on the 12 h pill's average | 276 (58%) | -0.024 | -11.3 | -0.031 | -0.56 | 30.2 |

## NZDCAD (549 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 549 (100%) | -0.168 | -92.3 | +0.000 | +0.00 | 96.4 |
| enter at 4 h always | 480 (87%) | -0.195 | -107.0 | -0.027 | -0.83 | 116.9 |
| 4 h check only (else skip) | 266 (48%) | -0.065 | -35.9 | +0.103 | +1.98 | 49.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 344 (63%) | -0.074 | -40.5 | +0.094 | +2.32 | 49.2 |
| the rule, checked at H4 closes only | 340 (62%) | -0.055 | -30.1 | +0.113 | +2.72 | 43.5 |
| the rule on the 12 h pill's average | 346 (63%) | -0.097 | -53.2 | +0.071 | +1.62 | 61.8 |

## NZDUSD (593 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 593 (100%) | -0.143 | -84.9 | +0.000 | +0.00 | 105.2 |
| enter at 4 h always | 523 (88%) | -0.156 | -92.5 | -0.013 | -0.33 | 117.1 |
| 4 h check only (else skip) | 262 (44%) | -0.059 | -35.2 | +0.084 | +1.53 | 57.0 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 355 (60%) | -0.101 | -60.1 | +0.042 | +0.91 | 86.7 |
| the rule, checked at H4 closes only | 350 (59%) | -0.106 | -63.0 | +0.037 | +0.79 | 84.9 |
| the rule on the 12 h pill's average | 352 (59%) | -0.074 | -44.1 | +0.069 | +1.50 | 69.0 |

## US100 (381 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 381 (100%) | +0.106 | +40.4 | +0.000 | +0.00 | 32.2 |
| enter at 4 h always | 339 (89%) | +0.113 | +43.2 | +0.007 | +0.15 | 29.1 |
| 4 h check only (else skip) | 187 (49%) | +0.119 | +45.5 | +0.013 | +0.20 | 17.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 251 (66%) | +0.118 | +45.1 | +0.012 | +0.24 | 17.1 |
| the rule, checked at H4 closes only | 244 (64%) | +0.136 | +51.8 | +0.030 | +0.57 | 16.3 |
| the rule on the 12 h pill's average | 224 (59%) | +0.151 | +57.4 | +0.045 | +0.81 | 13.8 |

## US500 (441 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 441 (100%) | +0.099 | +43.6 | +0.000 | +0.00 | 49.2 |
| enter at 4 h always | 386 (88%) | +0.093 | +41.0 | -0.006 | -0.12 | 42.2 |
| 4 h check only (else skip) | 200 (45%) | +0.004 | +1.7 | -0.095 | -1.34 | 47.1 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 277 (63%) | -0.002 | -1.0 | -0.101 | -1.75 | 45.9 |
| the rule, checked at H4 closes only | 268 (61%) | +0.007 | +2.9 | -0.092 | -1.56 | 41.3 |
| the rule on the 12 h pill's average | 263 (60%) | +0.037 | +16.2 | -0.062 | -1.02 | 27.6 |

## USDCAD (591 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 591 (100%) | -0.156 | -92.3 | +0.000 | +0.00 | 130.4 |
| enter at 4 h always | 515 (87%) | -0.190 | -112.6 | -0.034 | -1.08 | 132.1 |
| 4 h check only (else skip) | 258 (44%) | -0.034 | -20.1 | +0.122 | +2.33 | 46.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 348 (59%) | -0.071 | -42.0 | +0.085 | +1.96 | 65.6 |
| the rule, checked at H4 closes only | 336 (57%) | -0.058 | -34.5 | +0.098 | +2.19 | 60.0 |
| the rule on the 12 h pill's average | 350 (59%) | -0.080 | -47.2 | +0.076 | +1.67 | 70.5 |

## USDJPY (162 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 162 (100%) | +0.033 | +5.4 | +0.000 | +0.00 | 23.1 |
| enter at 4 h always | 147 (91%) | +0.076 | +12.4 | +0.043 | +0.40 | 19.5 |
| 4 h check only (else skip) | 72 (44%) | +0.101 | +16.4 | +0.068 | +0.64 | 9.3 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 106 (65%) | +0.083 | +13.5 | +0.050 | +0.64 | 12.0 |
| the rule, checked at H4 closes only | 103 (64%) | +0.091 | +14.7 | +0.058 | +0.72 | 10.0 |
| the rule on the 12 h pill's average | 104 (64%) | +0.047 | +7.7 | +0.014 | +0.17 | 10.6 |

## USOIL (504 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 504 (100%) | +0.000 | +0.0 | +0.000 | +0.00 | 50.6 |
| enter at 4 h always | 422 (84%) | -0.019 | -9.4 | -0.019 | -0.43 | 46.0 |
| 4 h check only (else skip) | 235 (47%) | -0.038 | -19.2 | -0.038 | -0.60 | 28.1 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 315 (62%) | -0.042 | -21.4 | -0.043 | -0.82 | 32.8 |
| the rule, checked at H4 closes only | 304 (60%) | +0.025 | +12.7 | +0.025 | +0.37 | 26.9 |
| the rule on the 12 h pill's average | 309 (61%) | -0.031 | -15.5 | -0.031 | -0.58 | 36.8 |

## XAUUSD (166 signals)

| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal (entered at the signal) | 166 (100%) | +0.193 | +32.0 | +0.000 | +0.00 | 23.8 |
| enter at 4 h always | 151 (91%) | +0.180 | +29.9 | -0.013 | -0.22 | 22.8 |
| 4 h check only (else skip) | 80 (48%) | +0.066 | +10.9 | -0.127 | -1.14 | 14.9 |
| THE RULE: 4 h check, then wait for a positive average up to 24 h | 111 (67%) | +0.102 | +16.9 | -0.091 | -1.08 | 12.3 |
| the rule, checked at H4 closes only | 111 (67%) | +0.090 | +15.0 | -0.102 | -1.19 | 12.1 |
| the rule on the 12 h pill's average | 111 (67%) | +0.128 | +21.3 | -0.064 | -0.73 | 12.8 |
