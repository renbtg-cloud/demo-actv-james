package demo;

import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** Runs with core.jar + adapter-httpjdk.jar ONLY; no Postgres, Rabbit or JDBC classes. */
public final class HttpAdapterContract {
  static int pass=0,fail=0;
  static void check(boolean ok,String name){
    if(ok){pass++;System.out.println("PASS "+pass+"/"+(pass+fail)+" "+name);}
    else {fail++;System.err.println("FAIL "+fail+"/"+(pass+fail)+" "+name);}
  }
  static final String READ="test-reader-token-01234567890",WRITE="test-writer-token-01234567890";
  static final class FakeRequests implements RequestOperations {
    final Map<String,Detail> byExternal=new ConcurrentHashMap<>();
    final Map<String,Detail> byId=new ConcurrentHashMap<>();
    public synchronized Admission create(String external,String customer,String product)throws DataAccessUnavailable{
      if(external.equals("unavailable"))throw new DataAccessUnavailable(new IllegalStateException("simulated storage outage"));
      if(external.equals("commit-uncertain"))return new Admission(AdmissionStatus.UNCERTAIN,null);
      Detail prev=byExternal.get(external);
      if(prev!=null){
        return new Admission(prev.customerId().equals(customer)&&prev.productCode().equals(product)
          ?AdmissionStatus.REPLAY:AdmissionStatus.CONFLICT,
          prev.customerId().equals(customer)&&prev.productCode().equals(product)?prev.id():null);
      }
      Detail d=new Detail(UUID.randomUUID().toString(),external,customer,product,"READY",0,0,0,0);
      byExternal.put(external,d);byId.put(d.id(),d);
      return new Admission(AdmissionStatus.CREATED,d.id());
    }
    public Detail find(String id){return byId.get(id);}
  }
  record Response(int code,String body,String location){}
  static Response call(HttpClient client,int port,String method,String path,String body,String token,String contentType)throws Exception{
    return call(client,port,method,path,body,token,contentType,null);
  }
  static Response call(HttpClient client,int port,String method,String path,String body,String token,String contentType,String run)throws Exception{
    HttpRequest.Builder b=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+path)).timeout(Duration.ofSeconds(5));
    if(token!=null)b.header("Authorization","Bearer "+token);
    if(run!=null)b.header("X-Deroyque-Load-Run",run);
    if(body!=null){if(contentType!=null)b.header("Content-Type",contentType); b.method(method,HttpRequest.BodyPublishers.ofString(body));}
    else b.method(method,HttpRequest.BodyPublishers.noBody());
    HttpResponse<String> r=client.send(b.build(),HttpResponse.BodyHandlers.ofString());
    return new Response(r.statusCode(),r.body(),r.headers().firstValue("Location").orElse(null));
  }
  public static void main(String[] args)throws Exception{
    var service=new FakeRequests();
    var api=new RestApi(service,READ,WRITE);
    HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),8);
    server.createContext("/",api::handle);
    server.start();
    int port=server.getAddress().getPort();
    HttpClient client=HttpClient.newHttpClient();
    String path="/api/v1/requests";
    String body="{\"externalId\":\"X1\",\"customerId\":\"C1\",\"productCode\":\"P1\"}";
    try{
      check(call(client,port,"GET",path+"/missing",null,null,null).code()==401,"GET requires read token");
      check(call(client,port,"GET",path+"/missing",null,"incorrect",null).code()==401,"wrong token refused");
      check(call(client,port,"GET",path+"/missing",null,READ,null).code()==404,"unknown id is 404");
      check(call(client,port,"POST",path,body,null,"application/json").code()==401,"POST requires write token");
      check(call(client,port,"POST",path,body,READ,"application/json").code()==401,"read token cannot create");
      check(call(client,port,"POST",path,body,WRITE,"text/plain").code()==415,"content type checked");
      check(call(client,port,"POST",path,"not-json",WRITE,"application/json").code()==400,"malformed JSON rejected");
      check(call(client,port,"POST",path,"{\"externalId\":\"1\",\"externalId\":\"2\"}",WRITE,"application/json").code()==400,"duplicate JSON keys rejected");
      check(call(client,port,"POST",path,"{}",WRITE,"application/json").code()==400,"required fields enforced");
      check(call(client,port,"POST",path," ".repeat(4097),WRITE,"application/json").code()==413,"body size limit enforced");
      Response created=call(client,port,"POST",path,body,WRITE,"application/json");
      check(created.code()==202 && created.location()!=null && created.body().contains("\"id\""),"created 202 with location");
      check(created.location()!=null && created.location().startsWith(path+"/"),"location follows public route");
      Response replay=call(client,port,"POST",path,body,WRITE,"application/json");
      check(replay.code()==200 && replay.location().equals(created.location()) && replay.body().contains("\"idempotentReplay\":true"),"replay retains identity");
      check(service.byExternal.size()==1,"replay does not create another row");
      String conflicted="{\"externalId\":\"X1\",\"customerId\":\"C1\",\"productCode\":\"different\"}";
      check(call(client,port,"POST",path,conflicted,WRITE,"application/json").code()==409,"conflicting identity rejected");
      check(call(client,port,"GET",created.location(),null,WRITE,null).code()==401,"write token cannot read");
      Response found=call(client,port,"GET",created.location(),null,READ,null);
      check(found.code()==200 && found.body().contains("\"customerId\":\"C1\"") && found.body().contains("\"status\":\"READY\""),"GET maps DTO without database classes");
      check(call(client,port,"PUT",path,body,WRITE,"application/json").code()==405,"unsupported method 405");
      check(call(client,port,"GET","/other",null,READ,null).code()==404,"unknown route 404");
      check(call(client,port,"POST",path,"{\"externalId\":\"commit-uncertain\",\"customerId\":\"C\",\"productCode\":\"P\"}",WRITE,"application/json").code()==503,"uncertain COMMIT is 503");
      check(call(client,port,"POST",path,"{\"externalId\":\"unavailable\",\"customerId\":\"C\",\"productCode\":\"P\"}",WRITE,"application/json").code()==503,"database unavailable is 503");
      check(call(client,port,"GET",path+"/"+"X".repeat(41),null,READ,null).code()==400,"invalid identifier blocked");
      check(call(client,port,"GET","/api/v1/lab/ingress-metrics?run=test-run",null,READ,null).code()==404,
            "metrics are opt-in and disabled by default");
      // Even valid load-tagged traffic must not activate the disabled collector.
      String noMetricsBody="{\"externalId\":\"disabled-run-0\",\"customerId\":\"C\",\"productCode\":\"P\"}";
      check(call(client,port,"POST",path,noMetricsBody,WRITE,"application/json","disabled-run").code()==202,
            "default-off adapter admits tagged traffic normally");
      check(call(client,port,"GET","/api/v1/lab/ingress-metrics?run=disabled-run",null,READ,null).code()==404,
            "metrics endpoint stays absent after tagged traffic with default-off configuration");
      // Opt-in metrics are verified using the in-memory adapter without JDBC or broker.
      var instrumented=new RestApi(new FakeRequests(),READ,WRITE,true);
      HttpServer lab=HttpServer.create(new InetSocketAddress("127.0.0.1",0),32);
      lab.createContext("/",instrumented::handle);lab.start();
      int lport=lab.getAddress().getPort();
      try {
        String route="/api/v1/lab/ingress-metrics?run=lab-run";
        check(call(client,lport,"GET",route,null,null,null).code()==401,"metrics require read token");
        check(call(client,lport,"GET",route,null,WRITE,null).code()==401,"write token cannot access metrics");
        check(call(client,lport,"GET","/api/v1/lab/ingress-metrics?run=bad%20id",null,READ,null).code()==400,"invalid metrics run refused");
        String payload="{\"externalId\":\"lab-run-0\",\"customerId\":\"C\",\"productCode\":\"P\"}";
        check(call(client,lport,"POST",path,payload,WRITE,"application/json","lab-run").code()==202,"tagged original recorded");
        check(call(client,lport,"POST",path,payload,WRITE,"application/json","lab-run").code()==200,"tagged replay recorded");
        String conflict="{\"externalId\":\"lab-run-0\",\"customerId\":\"C\",\"productCode\":\"DIFFERENT\"}";
        check(call(client,lport,"POST",path,conflict,WRITE,"application/json","lab-run").code()==409,"tagged conflict recorded");
        check(call(client,lport,"POST",path,payload,WRITE,"application/json","another-run").code()==200,"misbound tag does not alter counts");
        check(call(client,lport,"POST",path,payload,WRITE,"application/json",null).code()==200,"untagged request does not alter counts");
        String stats=call(client,lport,"GET",route,null,READ,null).body();
        check(stats.contains("\"started\":3")&&stats.contains("\"finished\":3")&&stats.contains("\"http_202\":1")&&stats.contains("\"http_200\":1")&&stats.contains("\"http_409\":1"),"three server-side outcomes match run");
        check(stats.contains("\"inflight\":0")&&stats.contains("\"peak_inflight\":")&&stats.contains("\"first_start_elapsed_ns\":"),"single JVM monotonic timing recorded");
        check(call(client,lport,"GET","/api/v1/lab/ingress-metrics?run=other-run",null,READ,null).body().contains("\"started\":0"),"unrelated run cannot inherit counts");
      }finally{lab.stop(0);}
      // Concurrent first sighting of many runs must not exceed the memory bound.
      IngressMeasurements bounded = new IngressMeasurements();
      var executor = java.util.concurrent.Executors.newFixedThreadPool(16);
      var gate = new java.util.concurrent.CountDownLatch(1);
      var futures = new java.util.ArrayList<java.util.concurrent.Future<Boolean>>();
      for(int n=0;n<256;n++){
        final int number=n;
        futures.add(executor.submit(()->{
          gate.await();
          var ticket=bounded.begin("limit-"+number);
          if(ticket!=null)bounded.finish(ticket,202);
          return ticket!=null;
        }));
      }
      gate.countDown();int accepted=0;
      for(var future:futures)if(future.get())accepted++;
      executor.shutdown();
      check(accepted==128,"concurrent run registrations strictly capped at 128");
    }finally{server.stop(0);}
    System.out.printf("RESULT FAILURES %d/%d PASSED %d/%d%n",fail,pass+fail,pass,pass+fail);
    if(fail>0)System.exit(1);
  }
}
