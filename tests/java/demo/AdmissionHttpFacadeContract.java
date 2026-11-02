package demo;

import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Real, dependency-free tests of the MVC adapter's shared application boundary. */
public final class AdmissionHttpFacadeContract {
  static final String READ="read-token-for-deroyque-tests-1234567",WRITE="write-token-for-deroyque-tests-1234567";
  static int checks=0,failures=0;
  static void check(boolean ok,String name){checks++;if(!ok)failures++;System.out.println((ok?"PASS":"FAIL")+" "+checks+"/"+checks+" "+name);}
  static byte[] body(String id,String customer,String product){return ("{\"externalId\":\""+id+"\",\"customerId\":\""+customer+"\",\"productCode\":\""+product+"\"}").getBytes(StandardCharsets.UTF_8);}
  static class Fake implements RequestOperations {
    record State(String id,String customer,String product){}
    final Map<String,State> data=new ConcurrentHashMap<>();
    final AtomicInteger creates=new AtomicInteger();
    volatile boolean fails=false,uncertain=false;
    @Override public Admission create(String key,String customer,String product) throws DataAccessUnavailable{
      if(fails)throw new DataAccessUnavailable(new RuntimeException("network"));
      if(uncertain)return new Admission(AdmissionStatus.UNCERTAIN,null);
      State candidate=new State(UUID.randomUUID().toString(),customer,product);
      State prior=data.putIfAbsent(key,candidate);
      if(prior==null){creates.incrementAndGet();return new Admission(AdmissionStatus.CREATED,candidate.id());}
      if(prior.customer().equals(customer)&&prior.product().equals(product))return new Admission(AdmissionStatus.REPLAY,prior.id());
      return new Admission(AdmissionStatus.CONFLICT,null);
    }
    @Override public Detail find(String id)throws DataAccessUnavailable{
      if(fails)throw new DataAccessUnavailable(new RuntimeException("network"));
      return data.entrySet().stream().filter(e->e.getValue().id().equals(id)).findFirst()
        .map(e->new Detail(id,e.getKey(),e.getValue().customer(),e.getValue().product(),"READY",0,0,0,0)).orElse(null);
    }
  }
  static int send(AdmissionHttpFacade f,String key,String customer,String product){return f.create("Bearer "+WRITE,"application/json",body(key,customer,product),key.split("-")[0]).status();}
  public static void main(String[] args)throws Exception{
    Fake db=new Fake();AdmissionHttpFacade f=new AdmissionHttpFacade(db,READ,WRITE,false);
    var input=body("same-key","A","B");
    check(f.create(null,"application/json",input,null).status()==401,"creation needs token");
    check(f.create("Bearer "+READ,"application/json",input,null).status()==401,"read token cannot create");
    check(f.create("Bearer "+WRITE,"text/plain",input,null).status()==415,"content type rejected");
    check(f.create("Bearer "+WRITE,"application/json",new byte[4097],null).status()==413,"oversized body rejected");
    check(f.create("Bearer "+WRITE,"application/json","not json".getBytes(),null).status()==400,"malformed JSON rejected");
    check(f.create("Bearer "+WRITE,"application/json",new byte[]{(byte)0xc3,(byte)0x28},null).status()==400,
          "malformed UTF-8 sequence rejected without replacement");
    check(f.create("Bearer "+WRITE,"application/json",new byte[]{(byte)0xed,(byte)0xa0,(byte)0x80},null).status()==400,
          "UTF-8 surrogate encoding rejected");
    check(f.create("Bearer "+WRITE,"application/json",body("\\uD800", "A", "B"),null).status()==400,
          "lone high-surrogate JSON escape rejected");
    check(f.create("Bearer "+WRITE,"application/json",body("\\uDC00", "A", "B"),null).status()==400,
          "lone low-surrogate JSON escape rejected");
    check(f.create("Bearer "+WRITE,"application/json",body("\\uD800\\u0041", "A", "B"),null).status()==400,
          "high surrogate followed by ordinary character rejected");
    check(f.create("Bearer "+WRITE,"application/json",body("\\uD83D\\uDE80", "A", "B"),null).status()==202,
          "valid supplementary Unicode scalar from escape pair accepted");
    check(f.create("Bearer "+WRITE,"application/json; charset=UTF-8",body("ct-utf8","A","B"),null).status()==202,
          "UTF-8 charset accepted");
    check(f.create("Bearer "+WRITE,"application/json; charset=\"utf-8\"",body("ct-quoted","A","B"),null).status()==202,
          "quoted UTF-8 charset accepted");
    check(f.create("Bearer "+WRITE,"application/json; charset=ISO-8859-1",body("ct-reject","A","B"),null).status()==415,
          "non-UTF-8 charset rejected");
    check(f.create("Bearer "+WRITE,"application/json; charset=utf-8; charset=iso-8859-1",body("ct-duplicate","A","B"),null).status()==415,
          "conflicting charset declarations rejected");
    check(f.create("Bearer "+WRITE,"application/json; charset=",body("ct-empty","A","B"),null).status()==415,
          "empty charset declaration rejected");
    check(f.create("Bearer "+WRITE,"application/json; profile=v1",body("ct-profile","A","B"),null).status()==202,
          "well-formed unrelated media-type parameter preserves compatibility");

    check(f.create("Bearer "+WRITE,"application/json","{\"externalId\":\"x\",\"externalId\":\"x\"}".getBytes(),null).status()==400,"duplicate JSON keys rejected");
    check(f.create("Bearer "+WRITE,"application/json","{}".getBytes(),null).status()==400,"missing request properties rejected");
    check(f.create("Bearer "+WRITE,"application/json",input,null).status()==202,"creation maps to 202");
    check(db.creates.get()==5,"five durable admissions after Unicode and charset checks");
    var replay=f.create("Bearer "+WRITE,"application/json",input,null);
    check(replay.status()==200&&replay.json().contains("\"idempotentReplay\":true"),"replay response maps to 200");
    check(f.create("Bearer "+WRITE,"application/json",body("same-key","C","B"),null).status()==409,"conflict maps to 409");
    check(db.creates.get()==5,"conflict creates no new record");

    // The same Unicode scalar expressed as JSON escapes or literal UTF-8
    // must be the same durable idempotency identity, not two effects.
    var escapedUnicode=body("rocket-\\uD83D\\uDE80","A","B");
    var literalUnicode=body("rocket-🚀","A","B");
    check(f.create("Bearer "+WRITE,"application/json",escapedUnicode,null).status()==202,
          "escaped Unicode idempotency identity admitted");
    check(f.create("Bearer "+WRITE,"application/json",literalUnicode,null).status()==200,
          "equivalent literal UTF-8 Unicode identity replays instead of creating a new record");
    check(db.creates.get()==6,"equivalent Unicode encodings create exactly one idempotency identity");
    check(f.create("Bearer "+WRITE,"application/json",
          "{\"externalId\":\"bad\",\"exter\\u006ealId\":\"bad\",\"customerId\":\"A\",\"productCode\":\"B\"}".getBytes(StandardCharsets.UTF_8),null).status()==400,
          "duplicate JSON object key concealed by Unicode escape is rejected");

    String id=db.data.get("same-key").id();
    check(f.find("Bearer "+READ,id).status()==200,"read endpoint returns detail");
    check(f.find("Bearer "+READ,id).json().contains("\"status\":\"READY\""),"detail maps state");
    check(f.find("Bearer "+WRITE,id).status()==401,"write token cannot read");
    check(f.find("Bearer "+READ,"missing").status()==404,"missing record 404");
    check(f.find("Bearer "+READ," ").status()==400,"invalid ID blocked");
    check(f.measurements("Bearer "+READ,"load") .status()==404,"metrics disabled by default");
    db.uncertain=true;
    check(f.create("Bearer "+WRITE,"application/json",body("uncertain","A","B"),null).status()==503,"uncertain commit 503");
    db.uncertain=false;db.fails=true;
    check(f.create("Bearer "+WRITE,"application/json",body("offline","A","B"),null).status()==503,"database outage 503");
    check(f.find("Bearer "+READ,id).status()==503,"read database outage 503");
    db.fails=false;
    AdmissionHttpFacade monitored=new AdmissionHttpFacade(db,READ,WRITE,true);
    check(monitored.measurements("Bearer "+READ,"bad space").status()==400,"invalid metrics run rejected");
    check(monitored.measurements(null,"load").status()==401,"metrics require read token");
    check(monitored.measurements("Bearer "+WRITE,"load").status()==401,"metrics deny write token");
    var tagged=monitored.create("Bearer "+WRITE,"application/json",body("load-new","A","B"),"load");
    check(tagged.status()==202,"tagged creation accepted");
    check(monitored.create("Bearer "+WRITE,"application/json",body("load-new","A","B"),"load").status()==200,"tagged replay accepted");
    check(monitored.create("Bearer "+WRITE,"application/json",body("load-new","C","B"),"load").status()==409,"tagged conflict accepted as expected");
    check(monitored.measurements("Bearer "+READ,"load").json().contains("\"finished\":3"),"server counters collect all three outcomes");
    check(monitored.measurements("Bearer "+READ,"load").json().contains("\"http_409\":1"),"server counter records conflict");
    check(monitored.measurements("Bearer "+READ,"other").json().contains("\"finished\":0"),"metrics isolate run identifiers");
    // Simultaneous pressure on a single key: no synthetic monitor, real Java threads.
    int parallel=64;
    try(var pool=Executors.newFixedThreadPool(32)){
      List<Future<Integer>> results=new ArrayList<>();
      for(int i=0;i<parallel;i++)results.add(pool.submit(()->send(f,"parallel-key","A","B")));
      int created=0,replayed=0;for(var result:results){int code=result.get();if(code==202)created++;if(code==200)replayed++;}
      check(created==1,"64 concurrent admissions have exactly one winner");
      check(replayed==63,"remaining 63 arrivals idempotently replay");
    }
    check(db.creates.get()==8,"concurrent run adds one record after Unicode and media-type admissions");
    System.out.println("RESULT FAILURES "+failures+"/"+checks+" PASSED "+(checks-failures)+"/"+checks);
    if(failures!=0)System.exit(1);
  }
}
