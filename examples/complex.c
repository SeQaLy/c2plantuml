/* complex.c — 複雑な制御フローのストレステスト用サンプル
 *
 * - 5 重ループ           : deep_search
 * - 多数の分岐 (10 個超) : classify_token
 * - ネストループ + switch + do-while + goto : simulate
 */
#include <stdio.h>
#include <stddef.h>

#define N 5

/* ============================================================
 * 5 重ループ + 分岐 + 多重ループからの goto 脱出
 * ============================================================ */
int deep_search(int grid[N][N][N][N][N], int target)
{
    int found = 0;
    int checked = 0;

    for (int a = 0; a < N; a++) {
        for (int b = 0; b < N; b++) {
            for (int c = 0; c < N; c++) {
                for (int d = 0; d < N; d++) {
                    for (int e = 0; e < N; e++) {
                        int v = grid[a][b][c][d][e];
                        checked++;
                        if (v < 0) {
                            continue;
                        } else if (v == target) {
                            found++;
                            if (found >= 3) {
                                goto done;
                            }
                        } else if (v > target * 2) {
                            break;
                        } else {
                            found += 0;
                        }
                    }
                }
            }
        }
    }

done:
    printf("checked=%d\n", checked);
    return found;
}

/* ============================================================
 * 多数の分岐 (NULL チェック + switch 4 + 文字分類 6 + 後処理 4)
 * ============================================================ */
int classify_token(const char *s, int len, int mode)
{
    if (s == NULL || len <= 0) {
        return -1;
    }

    int score = 0;
    switch (mode) {
    case 0:
        score = len;
        break;
    case 1:
        score = len * 2;
        break;
    case 2:
    case 3:
        score = len * 3;
        break;
    default:
        score = 0;
        break;
    }

    for (int i = 0; i < len; i++) {
        char ch = s[i];
        if (ch >= '0' && ch <= '9') {
            score += 1;
        } else if (ch >= 'a' && ch <= 'z') {
            score += 2;
        } else if (ch >= 'A' && ch <= 'Z') {
            score += 3;
        } else if (ch == '_') {
            score += 4;
        } else if (ch == ' ' || ch == '\t') {
            continue;
        } else {
            score -= 1;
        }

        if (score > 100) {
            score = 100;
            break;
        }
    }

    if (score < 0) {
        return 0;
    } else if (score < 10) {
        return 1;
    } else if (score < 50) {
        return 2;
    } else {
        return 3;
    }
}

/* ============================================================
 * ネストループ + ループ内 switch + do-while + 内外への goto
 * ============================================================ */
int simulate(int steps, int seed)
{
    int state = seed;
    int total = 0;
    int phase = 0;

    while (steps-- > 0) {
        switch (phase) {
        case 0:
            for (int i = 0; i < 4; i++) {
                for (int j = 0; j < 4; j++) {
                    if ((state ^ i) & j) {
                        total += i * j;
                    } else if (state > 1000) {
                        phase = 2;
                        goto next;
                    } else {
                        total -= 1;
                    }
                }
            }
            phase = 1;
            break;
        case 1:
            do {
                state = state * 3 + 1;
                if (state % 2 == 0) {
                    state /= 2;
                } else if (state % 5 == 0) {
                    continue;
                }
                total += state & 0xFF;
            } while (state < 5000);
            phase = 0;
            break;
        default:
            total = -1;
            goto end;
        }
    next:
        state += phase;
    }

end:
    return total;
}
