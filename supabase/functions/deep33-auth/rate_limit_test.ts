import { IpWindowRateLimiter } from "./rate_limit.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

Deno.test("allows the configured burst and rejects the next request", () => {
  const limiter = new IpWindowRateLimiter(60_000, 30);
  for (let index = 0; index < 30; index += 1) {
    assert(limiter.consume("203.0.113.10", 1_000).allowed, `request ${index + 1} should pass`);
  }

  const blocked = limiter.consume("203.0.113.10", 1_000);
  assert(!blocked.allowed, "request 31 should be rate limited");
  assert(blocked.remaining === 0, "blocked requests must report zero remaining");
  assert(blocked.retryAfterSeconds === 60, "retry-after must cover the active window");
});

Deno.test("rate limits each IP independently", () => {
  const limiter = new IpWindowRateLimiter(60_000, 1);
  assert(limiter.consume("198.51.100.1", 10).allowed, "first IP should pass");
  assert(!limiter.consume("198.51.100.1", 10).allowed, "first IP should be limited");
  assert(limiter.consume("198.51.100.2", 10).allowed, "second IP should have its own bucket");
});

Deno.test("resets the bucket after the fixed window", () => {
  const limiter = new IpWindowRateLimiter(60_000, 1);
  assert(limiter.consume("192.0.2.1", 5_000).allowed, "initial request should pass");
  assert(!limiter.consume("192.0.2.1", 64_999).allowed, "request before reset should be blocked");
  assert(limiter.consume("192.0.2.1", 65_000).allowed, "request at reset should pass");
});

Deno.test("recovers safely if the local clock moves backwards", () => {
  const limiter = new IpWindowRateLimiter(60_000, 1);
  assert(limiter.consume("192.0.2.2", 10_000).allowed, "initial request should pass");
  assert(limiter.consume("192.0.2.2", 9_000).allowed, "backwards clock must reset instead of locking the IP");
});
