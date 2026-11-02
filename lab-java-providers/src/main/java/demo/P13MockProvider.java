package demo;

import com.sun.net.httpserver.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import java.util.concurrent.Executors;

/** Separate HTTP process, durable idempotent test provider (PostgreSQL only). NOT a real provider. */
public final class P13MockProvider {
  static String url=System.getenv().getOrDefault("P13_PROVIDER_JDBC","jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema=deroyque_p13_provider");
  static Connection db() throws SQLException {return DriverManager.getConnection(url,"nobody","");}
  static String get(Map<String,String> x,String k) {return x.getOrDefault(k,"");}
  static Map<String,String> form(String s){Map<String,String> m=new HashMap<>();for(String f:s.split("&")){String[]p=f.split("=",2);if(p.length==2)m.put(URLDecoder.decode(p[0],StandardCharsets.UTF_8),URLDecoder.decode(p[1],StandardCharsets.UTF_8));}return m;}
  static void answer(HttpExchange ex,int status,String body,String receipt)throws IOException{
    if(receipt!=null)ex.getResponseHeaders().add("X-Provider-Receipt",receipt);
    byte[]b=body.getBytes(StandardCharsets.UTF_8);ex.sendResponseHeaders(status,b.length);
    try(OutputStream os=ex.getResponseBody()){os.write(b);}
  }
  static void createSchema()throws SQLException{try(Connection c=db();Statement s=c.createStatement()){
    s.execute("CREATE TABLE IF NOT EXISTS p13_effects(idem_key VARCHAR(100) PRIMARY KEY, mode VARCHAR(100) NOT NULL, outcome VARCHAR(20) NOT NULL)");
    s.execute("CREATE TABLE IF NOT EXISTS p13_attempts(idem_key VARCHAR(100) PRIMARY KEY, attempt_count INTEGER NOT NULL)");
  }}
  static void activate(HttpExchange ex)throws IOException{
    if(!ex.getRequestMethod().equals("POST")){answer(ex,405,"METHOD",null);return;}
    Map<String,String>p=form(new String(ex.getRequestBody().readAllBytes(),StandardCharsets.UTF_8));
    String k=get(p,"key"),mode=get(p,"mode");
    if(k.isEmpty()||mode.isEmpty()){answer(ex,400,"MISSING",null);return;}
    try(Connection c=db()){
      c.setAutoCommit(false);
      int count;
      try(PreparedStatement st=c.prepareStatement("INSERT INTO p13_attempts(idem_key,attempt_count) VALUES (?,1) ON CONFLICT (idem_key) DO UPDATE SET attempt_count=p13_attempts.attempt_count+1 RETURNING attempt_count")){
        st.setString(1,k);try(ResultSet r=st.executeQuery()){r.next();count=r.getInt(1);}
      }
      if("before500".equals(mode)&&count==1){c.commit();answer(ex,503,"TRANSIENT_BEFORE_EFFECT",null);return;}
      String old=null,outcome=null;
      try(PreparedStatement st=c.prepareStatement("SELECT mode,outcome FROM p13_effects WHERE idem_key=?")){
        st.setString(1,k);try(ResultSet r=st.executeQuery()){if(r.next()){old=r.getString(1);outcome=r.getString(2);}}
      }
      if(old!=null&&!old.equals(mode)){c.commit();answer(ex,409,"IDEMPOTENCY_CONFLICT",null);return;}
      if(old==null){
        outcome="reject".equals(mode)?"REJECTED":"ACTIVATED";
        try(PreparedStatement st=c.prepareStatement("INSERT INTO p13_effects(idem_key,mode,outcome) VALUES(?,?,?) ON CONFLICT(idem_key) DO NOTHING")){
          st.setString(1,k);st.setString(2,mode);st.setString(3,outcome);st.executeUpdate();
        }
        // Re-read after conflict; two concurrent transactions may race on this key.
        try(PreparedStatement st=c.prepareStatement("SELECT mode,outcome FROM p13_effects WHERE idem_key=?")){
          st.setString(1,k);try(ResultSet r=st.executeQuery()){r.next();old=r.getString(1);outcome=r.getString(2);}
        }
        if(!old.equals(mode)){c.commit();answer(ex,409,"IDEMPOTENCY_CONFLICT",null);return;}
      }
      c.commit(); // remote effect durability boundary precedes HTTP response
      if("after500".equals(mode)&&count==1){answer(ex,503,"TRANSIENT_AFTER_DURABLE_EFFECT",null);return;}
      if("unconfigured599".equals(mode)){answer(ex,599,"UNCONFIGURED_STATUS_WITH_VALID_RECEIPT","MOCK:"+outcome+":"+k);return;}
      if("malformed".equals(mode)){answer(ex,200,"BAD_PROOF","MOCK:ACTIVATED:WRONG_KEY");return;}
      if("bare200".equals(mode)){answer(ex,200,"NO_RECEIPT",null);return;}
      int status="REJECTED".equals(outcome)?422:200;
      answer(ex,status,outcome,"MOCK:"+outcome+":"+k);
    }catch(Exception e){e.printStackTrace(System.err);answer(ex,500,"PROVIDER_DB_ERROR",null);}
  }
  public static void main(String[] args)throws Exception{
    Class.forName("org.postgresql.Driver");createSchema();
    int port=Integer.parseInt(System.getenv().getOrDefault("P13_PROVIDER_PORT","18082"));
    HttpServer s=HttpServer.create(new InetSocketAddress("127.0.0.1",port),64);
    s.createContext("/activate",P13MockProvider::activate);
    s.setExecutor(Executors.newVirtualThreadPerTaskExecutor());s.start();
    System.out.println("P13_PROVIDER_READY port="+port);System.out.flush();
  }
}
