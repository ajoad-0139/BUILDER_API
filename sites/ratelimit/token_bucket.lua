-- Atomic single-bucket token bucket.
-- Units are thousandths of a token; time is Redis server milliseconds.
-- KEYS[1] bucket hash (fields: tokens, last_refill)
-- ARGV[1] capacity (units), ARGV[2] refill rate (units per minute), ARGV[3] cost (units)
-- Returns {allowed, retry_after_ms, remaining_units, reset_ms}

local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local PERIOD_MS = 60000

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local function ms_for(units)
  return math.ceil(units * PERIOD_MS / rate)
end

local state = redis.call('HMGET', KEYS[1], 'tokens', 'last_refill')
local tokens, last = tonumber(state[1]), tonumber(state[2])

if tokens == nil or last == nil then
  tokens, last = capacity, now            
else
  if last > now then last = now end       
  local added = math.floor((now - last) * rate / PERIOD_MS)
  tokens = math.min(capacity, tokens + added)
  if tokens >= capacity then
    last = now
  elseif added > 0 then
    last = last + ms_for(added)           
  end
end

local allowed, retry = 0, 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
  redis.call('HSET', KEYS[1],
    'tokens', string.format('%d', tokens),
    'last_refill', string.format('%d', last))
  redis.call('PEXPIRE', KEYS[1], ms_for(capacity) + 1000)
else
  retry = math.max(1, last + ms_for(cost - tokens) - now)
end

local reset = 0
if tokens < capacity then
  reset = math.max(0, last + ms_for(capacity - tokens) - now)
end
return {allowed, retry, tokens, reset}