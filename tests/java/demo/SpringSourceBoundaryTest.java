package demo;
import org.springframework.http.ResponseEntity;
import jakarta.servlet.http.HttpServletRequest;
import java.io.*;
/** Checks the MVC methods directly against test-only API stand-ins.
 * Does not represent actual Spring Boot startup or servlet wiring.
 */
public class SpringSourceBoundaryTest {
 static int n=0,fail=0;
 static void ok(boolean b,String what){n++;if(!b)fail++;System.out.println((b?"PASS ":"FAIL ")+n+"/"+n+" "+what);}
 static final class Fake implements RequestOperations {
  int calls=0;
  public Admission create(String key,String customer,String product) {calls++;return new Admission(AdmissionStatus.CREATED,"created-id");}
  public Detail find(String id){return new Detail(id,"ext","cust","prod","DONE",1,2,0,1);}
 }
 static HttpServletRequest request(String txt,String type) {return new HttpServletRequest(){
  public InputStream getInputStream(){return new ByteArrayInputStream(txt.getBytes(java.nio.charset.StandardCharsets.UTF_8));}
  public String getContentType(){return type;}
 };}
 public static void main(String...args)throws Exception{
  Fake fake=new Fake();AdmissionHttpFacade logic=new AdmissionHttpFacade(fake,
   "read-token-for-deroyque-tests-1234567","write-token-for-deroyque-tests-1234567",false);
  // Motor constructed but not started: intentionally no external dependencies are opened.
  P13Service dormant=new P13Service();
  SpringRequestController ctrl=new SpringRequestController(logic,dormant);
  var body="{\"externalId\":\"A\",\"customerId\":\"B\",\"productCode\":\"C\"}";
  ResponseEntity<String> denied=ctrl.create(request(body,"application/json"),null,null);
  ok(denied.getStatusCodeValue()==401,"MVC method denies missing token");
  ResponseEntity<String> accepted=ctrl.create(request(body,"application/json"),"Bearer write-token-for-deroyque-tests-1234567",null);
  ok(accepted.getStatusCodeValue()==202,"MVC POST delegates to database-neutral boundary");
  ok("/api/v1/requests/created-id".equals(accepted.getHeader("Location")),"MVC sets Location header");
  ok(fake.calls==1,"MVC rejected call never touched business boundary");
  ok(ctrl.find("created-id","Bearer read-token-for-deroyque-tests-1234567").getStatusCodeValue()==200,"MVC status query works");
  ok(ctrl.ingress("run","Bearer read-token-for-deroyque-tests-1234567").getStatusCodeValue()==404,"MVC metrics off is 404");
  ok(ctrl.live().getStatusCodeValue()==200,"MVC liveness method works");
  ok(ctrl.ready().getStatusCodeValue()==503,"MVC readiness rejects nonstarted engine");
  ok(ctrl.create(request("A".repeat(5000),"application/json"),"Bearer write-token-for-deroyque-tests-1234567",null).getStatusCodeValue()==413,"MVC reads bounded body");
  System.out.println("RESULT FAILURES "+fail+"/"+n+" PASSED "+(n-fail)+"/"+n);
  if(fail>0)System.exit(1);
 }
}
