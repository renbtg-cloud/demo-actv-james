package demo;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** Opt-in laboratory ingress metrics; uses ONE service JVM's monotonic clock.
 * This is not authenticated event provenance and not a production metrics exporter.
 */
final class IngressMeasurements {
  private final String instanceId=UUID.randomUUID().toString();
  private final long origin=System.nanoTime();
  private final ConcurrentHashMap<String,Counts> runs=new ConcurrentHashMap<>();
  private static final class Counts {
    final AtomicLong started=new AtomicLong(),finished=new AtomicLong(),inflight=new AtomicLong(),peak=new AtomicLong();
    final AtomicLong first=new AtomicLong(),last=new AtomicLong(),created=new AtomicLong(),replayed=new AtomicLong(),conflicted=new AtomicLong(),other=new AtomicLong(),incomplete=new AtomicLong();
    final AtomicLong ge10=new AtomicLong(),ge100=new AtomicLong(),ge1000=new AtomicLong();
  }
  record Ticket(Counts counts,long start) {}
  static boolean validRun(String run){return run!=null && run.matches("[A-Za-z0-9_-]{1,48}");}
  Ticket begin(String run){
    if(!validRun(run))return null;
    Counts c=runs.get(run);
    if(c==null){
      // Serialize only unseen run registrations; the previous size()+putIfAbsent
      // race allowed bursts of distinct run IDs to exceed the 128-run limit.
      synchronized(runs){
        c=runs.get(run);
        if(c==null){
          if(runs.size()>=128)return null;
          c=new Counts();runs.put(run,c);
        }
      }
    }
    long now=System.nanoTime()-origin;
    c.first.compareAndSet(0,Math.max(1,now));
    c.started.incrementAndGet();
    long active=c.inflight.incrementAndGet();c.peak.accumulateAndGet(active,Math::max);
    return new Ticket(c,System.nanoTime());
  }
  void finish(Ticket t,int httpStatus){
    if(t==null)return;
    long elapsed=Math.max(0,System.nanoTime()-t.start());Counts c=t.counts();
    if(httpStatus==202)c.created.incrementAndGet();
    else if(httpStatus==200)c.replayed.incrementAndGet();
    else if(httpStatus==409)c.conflicted.incrementAndGet();
    else if(httpStatus==0)c.incomplete.incrementAndGet();
    else c.other.incrementAndGet();
    if(elapsed>=10_000_000L)c.ge10.incrementAndGet();
    if(elapsed>=100_000_000L)c.ge100.incrementAndGet();
    if(elapsed>=1_000_000_000L)c.ge1000.incrementAndGet();
    c.last.accumulateAndGet(System.nanoTime()-origin,Math::max);
    c.finished.incrementAndGet();c.inflight.decrementAndGet();
  }
  String snapshot(String run){
    if(!validRun(run))throw new IllegalArgumentException("bad run id");
    Counts c=runs.getOrDefault(run,new Counts());
    return "{\"schema_version\":1,\"instance_id\":"+StrictJson.quote(instanceId)+
       ",\"run_id\":"+StrictJson.quote(run)+",\"server_elapsed_ns\":"+(System.nanoTime()-origin)+
       ",\"started\":"+c.started.get()+",\"finished\":"+c.finished.get()+
       ",\"inflight\":"+c.inflight.get()+",\"peak_inflight\":"+c.peak.get()+
       ",\"first_start_elapsed_ns\":"+c.first.get()+",\"last_finish_elapsed_ns\":"+c.last.get()+
       ",\"http_202\":"+c.created.get()+",\"http_200\":"+c.replayed.get()+
       ",\"http_409\":"+c.conflicted.get()+",\"http_other\":"+c.other.get()+
       ",\"response_incomplete\":"+c.incomplete.get()+
       ",\"latency_ge_10ms\":"+c.ge10.get()+",\"latency_ge_100ms\":"+c.ge100.get()+
       ",\"latency_ge_1000ms\":"+c.ge1000.get()+"}";
  }
}
