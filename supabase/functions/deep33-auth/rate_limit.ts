export type IpRateLimitDecision = {
  allowed: boolean;
  remaining: number;
  retryAfterSeconds: number;
};

type Bucket = { startedAt: number; count: number };

export class IpWindowRateLimiter {
  private readonly buckets = new Map<string, Bucket>();

  constructor(
    private readonly windowMs: number,
    private readonly limit: number,
    private readonly maxBuckets = 5000,
  ) {
    if (!Number.isFinite(windowMs) || windowMs < 1) {
      throw new RangeError("windowMs must be a positive finite number");
    }
    if (!Number.isInteger(limit) || limit < 1) {
      throw new RangeError("limit must be a positive integer");
    }
    if (!Number.isInteger(maxBuckets) || maxBuckets < 1) {
      throw new RangeError("maxBuckets must be a positive integer");
    }
  }

  consume(rawKey: string, now = Date.now()): IpRateLimitDecision {
    const key = rawKey.trim() || "unknown";
    const current = this.buckets.get(key);

    if (
      !current ||
      now < current.startedAt ||
      now - current.startedAt >= this.windowMs
    ) {
      this.buckets.set(key, { startedAt: now, count: 1 });
      this.prune(now);
      return {
        allowed: true,
        remaining: this.limit - 1,
        retryAfterSeconds: 0,
      };
    }

    if (current.count >= this.limit) {
      return {
        allowed: false,
        remaining: 0,
        retryAfterSeconds: Math.max(
          1,
          Math.ceil((this.windowMs - (now - current.startedAt)) / 1000),
        ),
      };
    }

    current.count += 1;
    return {
      allowed: true,
      remaining: this.limit - current.count,
      retryAfterSeconds: 0,
    };
  }

  private prune(now: number): void {
    if (this.buckets.size <= this.maxBuckets) return;

    for (const [key, bucket] of this.buckets) {
      if (now < bucket.startedAt || now - bucket.startedAt >= this.windowMs) {
        this.buckets.delete(key);
      }
    }

    if (this.buckets.size <= this.maxBuckets) return;
    const oldest = [...this.buckets.entries()]
      .sort((a, b) => a[1].startedAt - b[1].startedAt)
      .slice(0, this.buckets.size - this.maxBuckets);
    for (const [key] of oldest) this.buckets.delete(key);
  }
}
