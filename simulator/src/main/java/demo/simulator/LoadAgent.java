package demo.simulator;

import java.io.BufferedWriter;
import java.io.BufferedReader;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Locale;
import java.util.Map;
import java.util.HashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Standalone input generator. No dependency on the service, broker, or mock provider. */
public final class LoadAgent {
  static final Pattern ID = Pattern.compile("\\\"id\\\"\\s*:\\s*\\\"([0-9a-zA-Z-]+)\\\"");
  private LoadAgent() {}
  static Map<String,String> args(String[] raw) {
    var out = new HashMap<String,String>();
    for(String a: raw) {
      if(!a.startsWith("--") || !a.contains("=")) throw new IllegalArgumentException("Expected --key=value: "+a);
      var pair=a.substring(2).split("=",2);
      if(out.putIfAbsent(pair[0],pair[1])!=null)throw new IllegalArgumentException("Duplicate option "+pair[0]);
    }
    return out;
  }
  static int requiredInt(Map<String,String> a,String name,int min) {
    int v = Integer.parseInt(a.getOrDefault(name,""));
    if(v<min)throw new IllegalArgumentException(name+" must be >= "+min);
    return v;
  }
  static int positiveInt(Map<String,String>a,String key,int fallback){
    int v=Integer.parseInt(a.getOrDefault(key,""+fallback));
    if(v<1)throw new IllegalArgumentException(key+" must be positive");
    return v;
  }
  static String json(String s){return "\""+s.replace("\\","\\\\").replace("\"","\\\"")+"\"";}
  static String payload(String run,int slot,int variant){
    // Identical key + identical variant MUST have the same bytes across all JVMs.
    return "{\"externalId\":"+json(run+"-"+slot)+",\"customerId\":"+json("c-"+slot+(variant==0?"":"-conflict"))+
      ",\"productCode\":"+json(variant==0?"standard":"different")+"}";
  }
  static int variant(int sequence,int keySpace,int conflictPercent){
    // First wave is uncontested baseline, later waves mix replays with conflicts.
    return sequence<keySpace || Math.floorMod(sequence*37+11,100)>=conflictPercent?0:1;
  }
  static String extractId(String body){Matcher m=ID.matcher(body);return m.find()?m.group(1):"";}
  static void run(String[] raw) throws Exception {
    Map<String,String> a=args(raw);
    int agent=requiredInt(a,"agent",0),agents=requiredInt(a,"agents",1),total=requiredInt(a,"requests",1),
      keys=requiredInt(a,"keys",1), concurrency=positiveInt(a,"concurrency",8),
      conflict=Integer.parseInt(a.getOrDefault("conflict-percent","20")), timeout=positiveInt(a,"timeout-ms",8000);
    if(agent>=agents||conflict<0||conflict>100)throw new IllegalArgumentException("Invalid agent or conflict-percent");
    String run=a.get("run"),output=a.get("output"),base=a.get("url"),token=System.getenv("DEROYQUE_SIM_WRITE_TOKEN");
    if(run==null||!run.matches("[A-Za-z0-9_-]{1,48}")||output==null||base==null||token==null||token.isBlank())
      throw new IllegalArgumentException("run/output/url and env DEROYQUE_SIM_WRITE_TOKEN required");
    URI uri=URI.create(base+"/api/v1/requests");
    if(!uri.getScheme().equals("http")&&!uri.getScheme().equals("https")) throw new IllegalArgumentException("HTTP(S) URL required");
    long startAt=Long.parseLong(a.getOrDefault("start-at-ms","0"));
    double rate=Double.parseDouble(a.getOrDefault("rate-per-agent","0"));
    if(!Double.isFinite(rate)||rate<0)throw new IllegalArgumentException("rate-per-agent >=0 required");
    String planPath=a.get("plan");
    int count;
    if(planPath==null) count=total/agents + (agent<total%agents?1:0);
    else {
      try(var lines=Files.lines(Path.of(planPath))) {count=(int)(lines.count()-1);}
      if(count<1)throw new IllegalArgumentException("Empty plan for agent="+agent);
    }
    if(count==0){try(var w=Files.newBufferedWriter(Path.of(output))){w.write("sequence\tslot\tvariant\tstatus\tlatency_ns\tid\terror\n");}return;}
    HttpClient client=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(timeout)).version(HttpClient.Version.HTTP_1_1).build();
    Semaphore limit=new Semaphore(concurrency);
    AtomicInteger inflight=new AtomicInteger(),peak=new AtomicInteger(),ioErrors=new AtomicInteger();
    AtomicLong dispatched=new AtomicLong();
    CountDownLatch done=new CountDownLatch(count);
    final long[] firstLast=new long[2];
    Object outputLock=new Object();
    try(BufferedWriter writer=Files.newBufferedWriter(Path.of(output),StandardCharsets.UTF_8);
        BufferedReader plan=planPath==null?null:Files.newBufferedReader(Path.of(planPath),StandardCharsets.US_ASCII);
        var pool=Executors.newVirtualThreadPerTaskExecutor()) {
      writer.write("sequence\tslot\tvariant\tstatus\tlatency_ns\tid\terror\tsend_epoch_ms\tend_epoch_ms\tschedule_lag_ms\n");
      long originNano=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(startAt-System.currentTimeMillis());
      if(plan!=null&&!"sequence\tslot\tvariant\toffset_ms".equals(plan.readLine()))
        throw new IllegalArgumentException("Plan header missing or invalid");
      for(int k=0;k<count;k++){
        int seq=k*agents+agent, slot=seq%keys, variant=variant(seq,keys,conflict);
        long targetNano=originNano;
        if(plan!=null){
          String line=plan.readLine();
          if(line==null)throw new IllegalArgumentException("Truncated plan, agent="+agent+" row="+k);
          String[] columns=line.split("\t",-1);
          if(columns.length!=4)throw new IllegalArgumentException("Invalid plan row columns");
          seq=Integer.parseInt(columns[0]);
          if(seq<0||seq>=total)throw new IllegalArgumentException("Invalid plan sequence "+seq);
          slot=Integer.parseInt(columns[1]);variant=Integer.parseInt(columns[2]);
          long offset=Long.parseLong(columns[3]);
          if(slot<0||slot>=keys||variant<0||variant>1||offset<0)
            throw new IllegalArgumentException("Invalid plan fields");
          targetNano=originNano+TimeUnit.MILLISECONDS.toNanos(offset);
        } else if(rate>0){targetNano+=TimeUnit.MILLISECONDS.toNanos(Math.round(k*1000.0/rate));}
        long remaining=targetNano-System.nanoTime();
        if(remaining>0)TimeUnit.NANOSECONDS.sleep(remaining);
        limit.acquire();
        final int sequence=seq, selectedSlot=slot, selectedVariant=variant;
        final long scheduledNano=targetNano;
        int concurrent=inflight.incrementAndGet();peak.accumulateAndGet(concurrent,Math::max);
        if(dispatched.incrementAndGet()==1)firstLast[0]=System.nanoTime();
        pool.submit(()->{
          int status=-1;String id="",error="";
          long begin=System.nanoTime(), sentEpoch=System.currentTimeMillis();
          double scheduleLagMs=Math.max(0,(begin-scheduledNano)/1_000_000.0);
          try {
            HttpRequest req=HttpRequest.newBuilder(uri).timeout(Duration.ofMillis(timeout))
              .header("Authorization","Bearer "+token).header("Content-Type","application/json")
              .header("X-Deroyque-Load-Run",run)
              .POST(HttpRequest.BodyPublishers.ofString(payload(run,selectedSlot,selectedVariant))).build();
            HttpResponse<String> resp=client.send(req,HttpResponse.BodyHandlers.ofString());
            status=resp.statusCode();
            if(status==202||status==200)id=extractId(resp.body());
          }catch(Exception e){error=e.getClass().getSimpleName();ioErrors.incrementAndGet();}
          finally {
            long elapsed=System.nanoTime()-begin, endEpoch=System.currentTimeMillis();
            synchronized(outputLock){try {
              writer.write(sequence+"\t"+selectedSlot+"\t"+selectedVariant+"\t"+status+"\t"+elapsed+"\t"+id+"\t"+error+"\t"+sentEpoch+"\t"+endEpoch+"\t"+scheduleLagMs+"\n");
            }catch(IOException e){ioErrors.incrementAndGet();System.err.println("RESULT_WRITE_FAILURE: "+e);}}
            synchronized(firstLast){firstLast[1]=Math.max(firstLast[1],System.nanoTime());}
            inflight.decrementAndGet();limit.release();done.countDown();
          }
        });
      }
      if(plan!=null&&plan.readLine()!=null)throw new IllegalArgumentException("Extra plan rows");
      if(!done.await(Math.max(90L,(long)(rate>0?count/rate:0)+timeout*2L/1000+30),TimeUnit.SECONDS))
        throw new IllegalStateException("agent completion timeout");
      writer.flush();
    }
    double wallSeconds=(firstLast[1]-firstLast[0])/1.0e9;
    System.out.printf(Locale.ROOT,"AGENT_SUMMARY agent=%d requested=%d peak=%d errors=%d wall_s=%.3f%n",agent,count,peak.get(),ioErrors.get(),wallSeconds);
    if(ioErrors.get()!=0)System.err.println("AGENT_WARNING IO or output failures="+ioErrors.get());
  }
  public static void main(String[] args){
    try{run(args);}catch(Exception ex){System.err.println("AGENT_FATAL "+ex);System.exit(2);}
  }
}
