package demo;

import java.net.Socket;
import java.util.UUID;

/** Real PG/RabbitMQ/independent-provider test of engine lifecycle WITHOUT any HTTP server.
 * It validates the runtime that a future actual Spring Boot container will manage.
 */
public final class HeadlessEngineSmoke {
  static int n=0,fail=0;
  static void check(boolean ok,String message){n++;if(!ok)fail++;System.out.println((ok?"PASS ":"FAIL ")+n+"/"+n+" "+message);}
  public static void main(String[] args)throws Exception{
    P13Service engine=new P13Service();
    try {
      engine.startEngine();
      check(engine.isReady(),"headless motor readiness checks PostgreSQL and RabbitMQ");
      int port=Integer.parseInt(System.getenv("P13_API_PORT"));
      boolean noListener=false;
      try(Socket probe=new Socket("127.0.0.1",port)) {noListener=false;}catch(java.net.ConnectException ex){noListener=true;}
      check(noListener,"startEngine does not bind the legacy HTTP listener");
      RequestOperations ops=engine.requestOperations();
      String key="m10-headless-"+UUID.randomUUID();
      RequestOperations.Admission created=ops.create(key,"customer-headless","product-headless");
      check(created.status()==RequestOperations.AdmissionStatus.CREATED,"new work admitted in PostgreSQL and durable outbox");
      RequestOperations.Admission repeat=ops.create(key,"customer-headless","product-headless");
      check(repeat.status()==RequestOperations.AdmissionStatus.REPLAY&&repeat.id().equals(created.id()),"repeat idempotently reuses durable identity");
      check(ops.create(key,"other-customer","product-headless").status()==RequestOperations.AdmissionStatus.CONFLICT,"conflicting identity rejected");
      RequestOperations.Detail result=null;
      for(int i=0;i<90;i++){
        Thread.sleep(300);result=ops.find(created.id());
        if(result!=null&&"DONE".equals(result.status()))break;
      }
      check(result!=null&&"DONE".equals(result.status()),"headless RabbitMQ workers reconcile signed provider effect to DONE");
      check(result!=null&&result.signedEvidenceCount()>=1,"signed external evidence persists in PostgreSQL");
    }finally{engine.close();}
    check(!engine.isReady(),"close shuts down workers and broker connection");
    System.out.println("FINAL FAILURES "+fail+"/"+n+" PASSED "+(n-fail)+"/"+n);
    if(fail>0)System.exit(1);
  }
}
