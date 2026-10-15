# Average cuts every 4 h for 24 h + a 15-min 1R trailing stop - trader 2026-10-15

5954 trades (open symbols; locked never loaded; 2025-26 bloc only), 41s. Rule engine (the Practice tab's / live plan runner's kernel), entry 100% at the signal, card R, model spread. Paired with raw on the same trades.

## All open signals (5954 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.038 | -226.8 | +0.000 | +0.00 | 21% | 313.7 |
| the six average cuts only | -0.018 | -106.6 | +0.020 | +1.33 | 22% | 178.7 |
| the 15-min 1R trail only | -0.038 | -227.1 | -0.000 | -0.00 | 35% | 241.3 |
| THE PLAN: cuts + trail | -0.029 | -175.0 | +0.009 | +0.39 | 33% | 187.4 |
| the plan without TP2 | -0.032 | -191.3 | +0.006 | +0.26 | 33% | 203.8 |

### Same, +1 pip round trip

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.070 | -413.9 | +0.000 | +0.00 | 21% | 456.1 |
| the six average cuts only | -0.049 | -293.7 | +0.020 | +1.33 | 21% | 332.0 |
| the 15-min 1R trail only | -0.070 | -414.1 | -0.000 | -0.00 | 34% | 420.2 |
| THE PLAN: cuts + trail | -0.061 | -362.0 | +0.009 | +0.39 | 31% | 367.0 |
| the plan without TP2 | -0.064 | -378.4 | +0.006 | +0.26 | 31% | 383.6 |

## How THE PLAN's trades ended (and raw on the same trades)

| exit | trades | plan R / trade | raw R / trade | plan - raw, total R |
|---|---:|---:|---:|---:|
| TP2 | 136 | +3.082 | +3.082 | +0.0 |
| stop -1R | 101 | -1.141 | -1.144 | +0.3 |
| trailed stop | 3553 | +0.001 | +0.017 | -59.0 |
| cut 4h | 530 | -0.405 | -0.537 | +70.0 |
| cut 8h | 633 | -0.290 | -0.275 | -9.7 |
| cut 12h | 509 | -0.145 | -0.146 | +0.3 |
| cut 16h | 320 | -0.052 | -0.149 | +31.0 |
| cut 20h | 143 | +0.043 | -0.110 | +21.9 |
| cut 24h | 29 | +0.028 | +0.131 | -3.0 |

Raw reached TP2 on 1246 trades; the plan kept 136 of them (the rest: trailed stop 765, cut 8h 101, cut 12h 95, cut 16h 58, cut 4h 54, cut 20h 29, cut 24h 8).

## Commodity bloc (3468 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.103 | -356.5 | +0.000 | +0.00 | 20% | 402.6 |
| the six average cuts only | -0.047 | -162.2 | +0.056 | +3.02 | 21% | 208.9 |
| the 15-min 1R trail only | -0.067 | -233.0 | +0.036 | +1.29 | 34% | 251.6 |
| THE PLAN: cuts + trail | -0.040 | -138.9 | +0.063 | +2.20 | 32% | 163.0 |
| the plan without TP2 | -0.045 | -155.0 | +0.058 | +2.03 | 32% | 173.4 |

## Oil (USOIL) (504 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.000 | +0.0 | +0.000 | +0.00 | 22% | 50.6 |
| the six average cuts only | +0.056 | +28.2 | +0.056 | +1.03 | 25% | 21.3 |
| the 15-min 1R trail only | -0.031 | -15.7 | -0.031 | -0.42 | 34% | 35.4 |
| THE PLAN: cuts + trail | +0.011 | +5.5 | +0.011 | +0.14 | 35% | 16.0 |
| the plan without TP2 | +0.008 | +4.2 | +0.008 | +0.10 | 35% | 16.4 |

## Precious metals (166 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.193 | +32.0 | +0.000 | +0.00 | 27% | 23.8 |
| the six average cuts only | +0.079 | +13.1 | -0.114 | -1.11 | 26% | 26.1 |
| the 15-min 1R trail only | +0.134 | +22.3 | -0.059 | -0.44 | 43% | 7.6 |
| THE PLAN: cuts + trail | +0.007 | +1.2 | -0.186 | -1.24 | 36% | 8.6 |
| the plan without TP2 | +0.001 | +0.1 | -0.192 | -1.28 | 36% | 8.6 |

## US indices (822 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.102 | +84.0 | +0.000 | +0.00 | 24% | 63.2 |
| the six average cuts only | +0.043 | +35.5 | -0.059 | -1.29 | 25% | 45.6 |
| the 15-min 1R trail only | +0.007 | +5.6 | -0.095 | -1.62 | 37% | 50.5 |
| THE PLAN: cuts + trail | -0.017 | -14.0 | -0.119 | -1.90 | 33% | 44.0 |
| the plan without TP2 | -0.020 | -16.8 | -0.123 | -1.95 | 33% | 43.9 |

## USD majors (832 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.010 | +8.4 | +0.000 | +0.00 | 22% | 63.5 |
| the six average cuts only | -0.037 | -31.2 | -0.048 | -1.09 | 20% | 63.4 |
| the 15-min 1R trail only | -0.007 | -6.2 | -0.017 | -0.30 | 37% | 34.8 |
| THE PLAN: cuts + trail | -0.042 | -34.8 | -0.052 | -0.82 | 32% | 38.1 |
| the plan without TP2 | -0.034 | -28.3 | -0.044 | -0.69 | 32% | 31.6 |

## Yen (162 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.033 | +5.4 | +0.000 | +0.00 | 22% | 23.1 |
| the six average cuts only | +0.061 | +9.9 | +0.028 | +0.30 | 27% | 23.1 |
| the 15-min 1R trail only | -0.000 | -0.1 | -0.034 | -0.25 | 37% | 13.1 |
| THE PLAN: cuts + trail | +0.037 | +6.0 | +0.004 | +0.03 | 40% | 10.5 |
| the plan without TP2 | +0.028 | +4.5 | -0.006 | -0.04 | 40% | 11.4 |

## Detector DeepFib (271 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.021 | -5.6 | +0.000 | +0.00 | 17% | 47.4 |
| the six average cuts only | -0.044 | -11.8 | -0.023 | -0.38 | 16% | 35.6 |
| the 15-min 1R trail only | -0.028 | -7.5 | -0.007 | -0.05 | 38% | 23.2 |
| THE PLAN: cuts + trail | -0.047 | -12.7 | -0.026 | -0.20 | 35% | 25.9 |
| the plan without TP2 | -0.050 | -13.5 | -0.029 | -0.22 | 35% | 25.9 |

## Detector EMArevQ (267 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.097 | +25.8 | +0.000 | +0.00 | 36% | 17.7 |
| the six average cuts only | +0.114 | +30.5 | +0.018 | +0.36 | 33% | 13.8 |
| the 15-min 1R trail only | +0.011 | +2.8 | -0.086 | -1.09 | 38% | 12.5 |
| THE PLAN: cuts + trail | +0.016 | +4.4 | -0.080 | -0.97 | 36% | 10.0 |
| the plan without TP2 | +0.015 | +4.0 | -0.082 | -0.96 | 36% | 13.5 |

## Detector SweepMSS (1204 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.025 | -30.6 | +0.000 | +0.00 | 25% | 66.4 |
| the six average cuts only | -0.017 | -20.3 | +0.009 | +0.27 | 24% | 35.4 |
| the 15-min 1R trail only | -0.058 | -69.7 | -0.032 | -0.80 | 33% | 78.8 |
| THE PLAN: cuts + trail | -0.033 | -39.4 | -0.007 | -0.17 | 31% | 47.7 |
| the plan without TP2 | -0.038 | -46.0 | -0.013 | -0.30 | 31% | 52.9 |

## Detector TrendCont (4212 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.051 | -216.4 | +0.000 | +0.00 | 19% | 348.5 |
| the six average cuts only | -0.025 | -105.0 | +0.026 | +1.41 | 21% | 192.3 |
| the 15-min 1R trail only | -0.036 | -152.8 | +0.015 | +0.57 | 35% | 166.1 |
| THE PLAN: cuts + trail | -0.030 | -127.4 | +0.021 | +0.77 | 33% | 140.8 |
| the plan without TP2 | -0.032 | -135.8 | +0.019 | +0.69 | 33% | 146.9 |

## BUY (3273 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.035 | -116.2 | +0.000 | +0.00 | 21% | 130.0 |
| the six average cuts only | -0.007 | -22.1 | +0.029 | +1.40 | 23% | 67.9 |
| the 15-min 1R trail only | -0.035 | -115.6 | +0.000 | +0.01 | 35% | 124.4 |
| THE PLAN: cuts + trail | -0.033 | -107.4 | +0.003 | +0.09 | 33% | 111.9 |
| the plan without TP2 | -0.038 | -124.1 | -0.002 | -0.08 | 33% | 126.6 |

## SELL (2681 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.041 | -110.6 | +0.000 | +0.00 | 21% | 236.8 |
| the six average cuts only | -0.032 | -84.6 | +0.010 | +0.43 | 21% | 166.9 |
| the 15-min 1R trail only | -0.042 | -111.5 | -0.000 | -0.01 | 34% | 148.1 |
| THE PLAN: cuts + trail | -0.025 | -67.6 | +0.016 | +0.48 | 33% | 109.2 |
| the plan without TP2 | -0.025 | -67.2 | +0.016 | +0.48 | 33% | 107.2 |

## 2012-2018 (2562 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.004 | -11.2 | +0.000 | +0.00 | 22% | 126.0 |
| the six average cuts only | +0.009 | +23.9 | +0.014 | +0.59 | 22% | 87.4 |
| the 15-min 1R trail only | -0.035 | -88.4 | -0.030 | -0.91 | 35% | 103.5 |
| THE PLAN: cuts + trail | -0.022 | -57.4 | -0.018 | -0.52 | 33% | 66.7 |
| the plan without TP2 | -0.027 | -69.9 | -0.023 | -0.66 | 33% | 77.6 |

## 2019-2024 (3039 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.046 | -140.4 | +0.000 | +0.00 | 21% | 171.9 |
| the six average cuts only | -0.025 | -76.7 | +0.021 | +0.99 | 22% | 116.1 |
| the 15-min 1R trail only | -0.044 | -133.7 | +0.002 | +0.07 | 34% | 136.9 |
| THE PLAN: cuts + trail | -0.036 | -110.8 | +0.010 | +0.31 | 32% | 114.6 |
| the plan without TP2 | -0.038 | -116.3 | +0.008 | +0.25 | 32% | 119.6 |

## 2025-2026 (commodity bloc only - the rest is research-locked) (353 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.213 | -75.2 | +0.000 | +0.00 | 18% | 82.4 |
| the six average cuts only | -0.152 | -53.8 | +0.061 | +1.13 | 21% | 53.8 |
| the 15-min 1R trail only | -0.014 | -5.0 | +0.199 | +2.39 | 40% | 16.3 |
| THE PLAN: cuts + trail | -0.019 | -6.8 | +0.194 | +2.25 | 39% | 16.6 |
| the plan without TP2 | -0.015 | -5.1 | +0.199 | +2.28 | 39% | 18.1 |

## AUDCAD (588 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.090 | -52.7 | +0.000 | +0.00 | 20% | 73.2 |
| the six average cuts only | +0.000 | +0.2 | +0.090 | +2.13 | 22% | 33.0 |
| the 15-min 1R trail only | -0.116 | -68.3 | -0.027 | -0.40 | 31% | 76.2 |
| THE PLAN: cuts + trail | -0.068 | -40.0 | +0.022 | +0.32 | 30% | 48.5 |
| the plan without TP2 | -0.074 | -43.3 | +0.016 | +0.23 | 30% | 49.5 |

## AUDNZD (596 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.045 | -26.8 | +0.000 | +0.00 | 21% | 89.4 |
| the six average cuts only | -0.050 | -29.9 | -0.005 | -0.11 | 19% | 75.6 |
| the 15-min 1R trail only | -0.174 | -103.8 | -0.129 | -1.86 | 30% | 114.9 |
| THE PLAN: cuts + trail | -0.123 | -73.4 | -0.078 | -1.08 | 29% | 76.1 |
| the plan without TP2 | -0.122 | -72.5 | -0.077 | -1.05 | 29% | 74.7 |

## AUDUSD (551 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.014 | -7.6 | +0.000 | +0.00 | 22% | 47.1 |
| the six average cuts only | -0.035 | -19.3 | -0.021 | -0.43 | 22% | 39.5 |
| the 15-min 1R trail only | +0.016 | +8.8 | +0.030 | +0.42 | 39% | 21.2 |
| THE PLAN: cuts + trail | +0.025 | +13.6 | +0.039 | +0.52 | 36% | 15.2 |
| the plan without TP2 | +0.027 | +15.1 | +0.041 | +0.55 | 36% | 15.2 |

## EURUSD (360 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.014 | +4.9 | +0.000 | +0.00 | 22% | 53.6 |
| the six average cuts only | +0.007 | +2.6 | -0.006 | -0.10 | 19% | 39.5 |
| the 15-min 1R trail only | -0.022 | -7.9 | -0.035 | -0.40 | 38% | 22.4 |
| THE PLAN: cuts + trail | -0.039 | -14.1 | -0.053 | -0.55 | 31% | 17.8 |
| the plan without TP2 | -0.035 | -12.6 | -0.049 | -0.50 | 31% | 18.7 |

## GBPUSD (472 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.007 | +3.5 | +0.000 | +0.00 | 21% | 40.1 |
| the six average cuts only | -0.072 | -33.8 | -0.079 | -1.29 | 21% | 61.6 |
| the 15-min 1R trail only | +0.004 | +1.7 | -0.004 | -0.05 | 37% | 24.6 |
| THE PLAN: cuts + trail | -0.044 | -20.7 | -0.051 | -0.61 | 33% | 24.2 |
| the plan without TP2 | -0.033 | -15.6 | -0.041 | -0.48 | 33% | 22.2 |

## NZDCAD (549 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.168 | -92.3 | +0.000 | +0.00 | 19% | 96.4 |
| the six average cuts only | -0.064 | -35.1 | +0.104 | +2.43 | 21% | 55.0 |
| the 15-min 1R trail only | -0.123 | -67.4 | +0.045 | +0.67 | 32% | 69.4 |
| THE PLAN: cuts + trail | -0.102 | -56.0 | +0.066 | +0.94 | 30% | 57.3 |
| the plan without TP2 | -0.106 | -58.0 | +0.063 | +0.89 | 30% | 59.3 |

## NZDUSD (593 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.143 | -84.9 | +0.000 | +0.00 | 19% | 105.2 |
| the six average cuts only | -0.072 | -42.9 | +0.071 | +1.58 | 20% | 54.5 |
| the 15-min 1R trail only | -0.049 | -29.1 | +0.094 | +1.42 | 35% | 32.6 |
| THE PLAN: cuts + trail | -0.007 | -4.1 | +0.136 | +2.03 | 34% | 19.4 |
| the plan without TP2 | -0.017 | -10.2 | +0.126 | +1.87 | 34% | 21.6 |

## US100 (381 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.106 | +40.4 | +0.000 | +0.00 | 24% | 32.2 |
| the six average cuts only | +0.035 | +13.3 | -0.071 | -1.05 | 27% | 24.9 |
| the 15-min 1R trail only | +0.009 | +3.3 | -0.097 | -1.11 | 37% | 33.5 |
| THE PLAN: cuts + trail | -0.017 | -6.5 | -0.123 | -1.33 | 35% | 29.3 |
| the plan without TP2 | -0.023 | -8.7 | -0.129 | -1.40 | 35% | 28.4 |

## US500 (441 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.099 | +43.6 | +0.000 | +0.00 | 24% | 49.2 |
| the six average cuts only | +0.050 | +22.2 | -0.048 | -0.78 | 23% | 35.5 |
| the 15-min 1R trail only | +0.005 | +2.2 | -0.094 | -1.18 | 37% | 23.3 |
| THE PLAN: cuts + trail | -0.017 | -7.5 | -0.116 | -1.36 | 32% | 24.8 |
| the plan without TP2 | -0.018 | -8.1 | -0.117 | -1.37 | 32% | 25.4 |

## USDCAD (591 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | -0.156 | -92.3 | +0.000 | +0.00 | 18% | 130.4 |
| the six average cuts only | -0.059 | -35.1 | +0.097 | +2.22 | 20% | 80.5 |
| the 15-min 1R trail only | +0.045 | +26.7 | +0.201 | +3.14 | 37% | 28.0 |
| THE PLAN: cuts + trail | +0.035 | +21.0 | +0.192 | +2.91 | 35% | 25.4 |
| the plan without TP2 | +0.024 | +14.0 | +0.180 | +2.72 | 35% | 25.7 |

## USDJPY (162 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.033 | +5.4 | +0.000 | +0.00 | 22% | 23.1 |
| the six average cuts only | +0.061 | +9.9 | +0.028 | +0.30 | 27% | 23.1 |
| the 15-min 1R trail only | -0.000 | -0.1 | -0.034 | -0.25 | 37% | 13.1 |
| THE PLAN: cuts + trail | +0.037 | +6.0 | +0.004 | +0.03 | 40% | 10.5 |
| the plan without TP2 | +0.028 | +4.5 | -0.006 | -0.04 | 40% | 11.4 |

## USOIL (504 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.000 | +0.0 | +0.000 | +0.00 | 22% | 50.6 |
| the six average cuts only | +0.056 | +28.2 | +0.056 | +1.03 | 25% | 21.3 |
| the 15-min 1R trail only | -0.031 | -15.7 | -0.031 | -0.42 | 34% | 35.4 |
| THE PLAN: cuts + trail | +0.011 | +5.5 | +0.011 | +0.14 | 35% | 16.0 |
| the plan without TP2 | +0.008 | +4.2 | +0.008 | +0.10 | 35% | 16.4 |

## XAUUSD (166 trades)

| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |
|---|---:|---:|---:|---:|---:|---:|
| raw signal | +0.193 | +32.0 | +0.000 | +0.00 | 27% | 23.8 |
| the six average cuts only | +0.079 | +13.1 | -0.114 | -1.11 | 26% | 26.1 |
| the 15-min 1R trail only | +0.134 | +22.3 | -0.059 | -0.44 | 43% | 7.6 |
| THE PLAN: cuts + trail | +0.007 | +1.2 | -0.186 | -1.24 | 36% | 8.6 |
| the plan without TP2 | +0.001 | +0.1 | -0.192 | -1.28 | 36% | 8.6 |
