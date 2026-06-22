/* c2plantuml の動作確認用サンプル
 * 各種の制御構造を網羅している。
 */
#include <stdio.h>
#include <string.h>

#define MAX 10

// 単純な if / else if / else
int classify(int x)
{
    if (x < 0) {
        return -1;
    } else if (x == 0) {
        return 0;
    } else {
        return 1;
    }
}

// for ループ + break / continue + switch
int scan(const int *a, int n)
{
    int sum = 0;
    for (int i = 0; i < n; i++) {
        if (a[i] < 0)
            continue;
        switch (a[i] % 3) {
        case 0:
            sum += 1;
            break;
        case 1:
        case 2:
            sum += 2;
            break;
        default:
            sum += 0;
            break;
        }
        if (sum > MAX) {
            break;
        }
    }
    return sum;
}

// while と do-while
void countdown(int n)
{
    while (n > 0) {
        printf("%d\n", n);
        n--;
    }

    do {
        printf("again\n");
        n++;
    } while (n < 3);
}

// goto / label を含む簡単な状態処理
int retry_connect(int tries)
{
    int attempt = 0;
retry:
    attempt++;
    if (!connect_once()) {
        if (attempt < tries) {
            goto retry;
        }
        return -1;
    }
    return attempt;
}
