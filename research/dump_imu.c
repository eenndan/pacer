// Dump GPS9 (lat,lon,speed2d,speed3d) and GYRO (z,x,y rad/s) and CORI (w,x,y,z
// quaternion) for the first `NPAY` payloads (1 payload = ~1 s) to CSV files, to
// compare heading sources.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Separate block: GPMF_mp4reader.h uses FILE without including <stdio.h>, so
// these must come after the system headers (clang-format sorts per block).
#include "GPMF_parser.h"
#include "demo/GPMF_mp4reader.h"

static int g_start = 0;
static void dump_stream(size_t mp4, uint32_t payloads, uint32_t fourcc,
                        const char *fname, int npay) {
  FILE *f = fopen(fname, "w");
  for (uint32_t i = g_start; i < payloads && (int)i < g_start + npay; i++) {
    uint32_t *pdata = GetPayload(mp4, GetPayloadResource(mp4, 0, 0), i);
    if (!pdata)
      continue;
    uint32_t psize = GetPayloadSize(mp4, i);
    double in = 0, out = 0;
    GetPayloadTime(mp4, i, &in, &out);
    GPMF_stream gs;
    if (GPMF_Init(&gs, pdata, psize) != GPMF_OK)
      continue;
    if (GPMF_OK == GPMF_FindNext(&gs, fourcc, GPMF_RECURSE_LEVELS)) {
      uint32_t samples = GPMF_Repeat(&gs);
      uint32_t elems = GPMF_ElementsInStruct(&gs);
      if (samples == 0)
        continue;
      double *buf = malloc(samples * elems * sizeof(double));
      GPMF_ScaledData(&gs, buf, samples * elems * sizeof(double), 0, samples,
                      GPMF_TYPE_DOUBLE);
      for (uint32_t s = 0; s < samples; s++) {
        double t = in + (out - in) * ((double)s / samples);
        fprintf(f, "%.5f", t);
        for (uint32_t e = 0; e < elems; e++)
          fprintf(f, ",%.6f", buf[s * elems + e]);
        fprintf(f, "\n");
      }
      free(buf);
    }
  }
  fclose(f);
}

int main(int argc, char **argv) {
  if (argc < 3) {
    printf("usage: dump_imu file.mp4 npayloads [start]\n");
    return 1;
  }
  int npay = atoi(argv[2]);
  if (argc >= 4)
    g_start = atoi(argv[3]);
  size_t mp4 =
      OpenMP4Source(argv[1], MOV_GPMF_TRAK_TYPE, MOV_GPMF_TRAK_SUBTYPE, 0);
  if (!mp4) {
    printf("cannot open\n");
    return 1;
  }
  uint32_t payloads = GetNumberPayloads(mp4);
  dump_stream(mp4, payloads, STR2FOURCC("GPS9"), "/tmp/claude/gps9.csv", npay);
  dump_stream(mp4, payloads, STR2FOURCC("GYRO"), "/tmp/claude/gyro.csv", npay);
  dump_stream(mp4, payloads, STR2FOURCC("CORI"), "/tmp/claude/cori.csv", npay);
  dump_stream(mp4, payloads, STR2FOURCC("ACCL"), "/tmp/claude/accl.csv", npay);
  printf("dumped %d payloads\n", npay);
  CloseSource(mp4);
  return 0;
}
