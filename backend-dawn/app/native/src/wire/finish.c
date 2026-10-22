/* The HTTP parser stays in web/exchange. This owns only accepted-fd teardown. */
#include "dawn_ffi.h"
DAWN_FFI_REQUIRE(1);
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int64_t monotonic_ms(void) {
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
  return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* FIN follows the flushed response before a bounded drain of late request bytes.
 * A proxy can finish reading the response and close its sending half promptly.
 * Pipes have no socket shutdown, but EOF and the same drain bounds still apply. */
dawn_adt *DAWN_EXTERN(site_finish_exchange)(void) {
  if (shutdown(STDOUT_FILENO, SHUT_WR) != 0 && errno != ENOTSOCK &&
      errno != ENOTCONN && errno != EPIPE && errno != ECONNRESET) {
    return dawn_ffi_err("site.wire.shutdown", "cannot half close response transport");
  }
  int64_t start = monotonic_ms();
  if (start < 0) return dawn_ffi_err("site.wire.clock", "cannot time input drain");
  int64_t bytes = 0;
  unsigned char block[65536];
  while (bytes < 8388608) {
    int64_t current = monotonic_ms();
    if (current < 0) return dawn_ffi_err("site.wire.clock", "cannot time input drain");
    int64_t remaining = 300 - (current - start);
    if (remaining <= 0) break;
    struct pollfd fd = { .fd = STDIN_FILENO, .events = POLLIN };
    int ready = poll(&fd, 1, (int)remaining);
    if (ready < 0 && errno == EINTR) continue;
    if (ready <= 0) break;
    if (!(fd.revents & (POLLIN | POLLHUP))) break;
    size_t capacity = (size_t)(8388608 - bytes);
    if (capacity > sizeof(block)) capacity = sizeof(block);
    ssize_t count = read(STDIN_FILENO, block, capacity);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) break;
    bytes += count;
  }
  return dawn_ffi_ok(dawn_ffi_box_int(bytes));
}

/* One process owns one exchange. Only its first transport write is a head. */
int64_t DAWN_EXTERN(site_wire_first_write)(void) {
  static int first = 1;
  int result = first;
  first = 0;
  return result;
}
