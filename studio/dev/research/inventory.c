// Inventory all GPMF streams (FourCC, samples/payload, rate) in a GoPro MP4.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "GPMF_parser.h"
#include "demo/GPMF_mp4reader.h"

int main(int argc, char **argv) {
    if (argc < 2) { printf("usage: inventory file.mp4\n"); return 1; }
    size_t mp4 = OpenMP4Source(argv[1], MOV_GPMF_TRAK_TYPE, MOV_GPMF_TRAK_SUBTYPE, 0);
    if (!mp4) { printf("cannot open %s\n", argv[1]); return 1; }
    uint32_t payloads = GetNumberPayloads(mp4);
    double total = GetDuration(mp4);
    printf("payloads=%u duration=%.3f s\n", payloads, total);

    // Aggregate per-FourCC total sample count across all payloads.
    // Use a simple list.
    char keys[64][5]; uint32_t totsamp[64]; int nk = 0;
    for (uint32_t i = 0; i < payloads; i++) {
        uint32_t *pdata = GetPayload(mp4, GetPayloadResource(mp4, 0, 0), i);
        if (!pdata) continue;
        uint32_t psize = GetPayloadSize(mp4, i);
        GPMF_stream gs;
        if (GPMF_Init(&gs, pdata, psize) != GPMF_OK) continue;
        while (GPMF_OK == GPMF_FindNext(&gs, GPMF_KEY_STREAM, GPMF_RECURSE_LEVELS)) {
            GPMF_stream find; GPMF_CopyState(&gs, &find);
            if (GPMF_OK == GPMF_SeekToSamples(&find)) {
                uint32_t key = GPMF_Key(&find);
                uint32_t samples = GPMF_Repeat(&find);
                char fc[5] = {0}; memcpy(fc, &key, 4);
                int idx = -1;
                for (int k = 0; k < nk; k++) if (memcmp(keys[k], fc, 4) == 0) { idx = k; break; }
                if (idx < 0 && nk < 64) { idx = nk++; memcpy(keys[idx], fc, 4); keys[idx][4]=0; totsamp[idx]=0; }
                if (idx >= 0) totsamp[idx] += samples;
            }
        }
    }
    printf("\n%-6s %12s %10s\n", "FourCC", "total_samp", "Hz(avg)");
    for (int k = 0; k < nk; k++)
        printf("%-6s %12u %10.2f\n", keys[k], totsamp[k], totsamp[k]/total);
    CloseSource(mp4);
    return 0;
}
