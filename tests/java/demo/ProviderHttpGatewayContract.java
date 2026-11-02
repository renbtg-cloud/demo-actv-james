package demo;

import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** JDK-only actual socket checks of provider transport behavior. No Spring stand-ins. */
public class ProviderHttpGatewayContract {
  static int n;
  static void check(boolean ok,String message){
    n++;if(!ok)throw new AssertionError("FAIL "+n+"/"+n+" "+message);
    System.out.println("PASS "+n+"/"+n+" "+message);
  }
  static Map<String,String> form(String raw) {
    Map<String,String> map=new HashMap<>();
    for(String pair:raw.split("&")){
      String[] bits=pair.split("=",2);
      if(bits.length==2)map.put(URLDecoder.decode(bits[0],StandardCharsets.UTF_8),URLDecoder.decode(bits[1],StandardCharsets.UTF_8));
    }
    return map;
  }
  public static void main(String[] args) throws Exception {
    AtomicReference<Map<String,String>> lastPost=new AtomicReference<>();
    AtomicReference<Map<String,String>> lastLookup=new AtomicReference<>();
    AtomicInteger posts=new AtomicInteger();AtomicInteger lookups=new AtomicInteger();
    HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),32);
    server.createContext("/activate", ex->{
      try{
        lastPost.set(form(new String(ex.getRequestBody().readAllBytes(),StandardCharsets.UTF_8)));
        posts.incrementAndGet();
        ex.getResponseHeaders().add("X-Provider-Receipt","receipt-123");
        ex.sendResponseHeaders(202,-1);
      }finally{ex.close();}
    });
    server.createContext("/lookup", ex->{
      try{
        lastLookup.set(form(ex.getRequestURI().getRawQuery()));lookups.incrementAndGet();
        ex.getResponseHeaders().add("X-Provider-Receipt","query-456");
        ex.getResponseHeaders().add("X-Provider-Attestation","signed-789");
        ex.sendResponseHeaders(200,-1);
      }finally{ex.close();}
    });
    server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());server.start();
    try {
      String host="http://127.0.0.1:"+server.getAddress().getPort();
      ProviderHttpGateway gateway=new ProviderHttpGateway(host+"/activate",host+"/lookup",false);
      var outcome=gateway.post("a+b /& é","public:premium");
      check(outcome.status()==202,"post returns actual HTTP status");
      check("receipt-123".equals(outcome.receipt()),"post preserves provider receipt");
      check("a+b /& é".equals(lastPost.get().get("key")),"post percent-encodes arbitrary key");
      check("ok".equals(lastPost.get().get("mode")),"public API maps to provider ok mode");
      var second=gateway.post("id2","after500");
      check("after500".equals(lastPost.get().get("mode")),"laboratory modes are preserved");
      check(posts.get()==2,"two POSTs reached HTTP server");
      var queried=gateway.lookup("a+b /& é");
      check(queried.status()==200 && "query-456".equals(queried.receipt()),"lookup status and receipt");
      check("a+b /& é".equals(lastLookup.get().get("key")),"lookup percent-encodes arbitrary key");
      check("".equals(queried.challenge()),"unsigned lookup has no nonce");
      check(!lastLookup.get().containsKey("nonce"),"unsigned lookup request excludes nonce");
      var signed=new ProviderHttpGateway(host+"/activate",host+"/lookup",true);
      var signedOne=signed.lookup("key-one");
      var signedTwo=signed.lookup("key-two");
      check("signed-789".equals(signedOne.attestation()),"signed lookup carries untrusted attestation for later verification");
      check(signedOne.challenge().length()==36,"signed lookup has UUID challenge");
      check(!signedOne.challenge().equals(signedTwo.challenge()),"successive signed lookup challenges differ");
      check(signedTwo.challenge().equals(lastLookup.get().get("nonce")),"returned challenge binds to outgoing nonce");
      check(lookups.get()==3,"three lookups reached HTTP server");
      var disabled=new ProviderHttpGateway(host+"/activate","",true);
      check(disabled.lookup("anything")==null,"disabled lookup yields no invented proof");
      var failed=new ProviderHttpGateway(host+"/activate","http://127.0.0.1:1/lookup",true);
      check(failed.lookup("anything")==null,"transport outage yields no proof of absence");
      check(lookups.get()==3,"disabled and unavailable lookup did not reach provider");
      System.out.println("RESULT FAILURES 0/"+n+" PASSED "+n+"/"+n);
    }finally{server.stop(0);}
  }
}
