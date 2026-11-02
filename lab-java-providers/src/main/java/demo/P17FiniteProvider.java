package demo;

import com.sun.net.httpserver.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import java.util.concurrent.Executors;

/** Real HTTP, separate JVM, PostgreSQL-backed provider with finite key retention.
 * DEMO ONLY. Provider effect journal intentionally remains after key-index expiration.
 */
public final class P17FiniteProvider {
  static final String URL=System.getenv().getOrDefault("P17_PROVIDER_JDBC", "jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema=deroyque_p17_provider");
  static long retention=Long.parseLong(System.getenv().getOrDefault("P17_PROVIDER_RETENTION_MS","1900"));
  static Connection open()throws SQLException{return DriverManager.getConnection(URL,"nobody","");}
  static String params(HttpExchange ex,String k)throws IOException {
    String body=new String(ex.getRequestBody().readAllBytes(),StandardCharsets.UTF_8);
    for(String field:body.split("&")){String[] pair=field.split("=",2);if(pair.length==2&&pair[0].equals(k))return URLDecoder.decode(pair[1],StandardCharsets.UTF_8);}
    return "";
  }
  static void answer(HttpExchange ex,int status,String receipt)throws IOException {
    if(receipt!=null)ex.getResponseHeaders().add("X-Provider-Receipt",receipt);
    byte[] bytes=("status="+status).getBytes(StandardCharsets.UTF_8);
    ex.sendResponseHeaders(status,bytes.length);
    try(OutputStream out=ex.getResponseBody()){out.write(bytes);}
  }
  static void setup() throws SQLException {try(Connection c=open();Statement s=c.createStatement()){
    s.execute("CREATE TABLE p15_provider_index(idem_key VARCHAR(120) PRIMARY KEY,generation INT NOT NULL,expires_at_ms BIGINT NOT NULL)");
    s.execute("CREATE TABLE p15_provider_effect(idem_key VARCHAR(120) NOT NULL,generation INT NOT NULL, outcome VARCHAR(20) NOT NULL, created_ms BIGINT NOT NULL, PRIMARY KEY(idem_key,generation))");
  }}
  static void handle(HttpExchange ex)throws IOException{
    if(!ex.getRequestMethod().equals("POST")){answer(ex,405,null);return;}
    String body=new String(ex.getRequestBody().readAllBytes(),StandardCharsets.UTF_8);
    Map<String,String> params=new HashMap<>();for(String part:body.split("&")){String[]pair=part.split("=",2);if(pair.length==2)params.put(pair[0],URLDecoder.decode(pair[1],StandardCharsets.UTF_8));}
    String key=params.getOrDefault("key",""),mode=params.getOrDefault("mode","ok");
    if(key.isBlank()||key.length()>120){answer(ex,400,null);return;}
    try(Connection c=open()){
      c.setAutoCommit(false);
      try(PreparedStatement p=c.prepareStatement("INSERT INTO p15_provider_index(idem_key,generation,expires_at_ms) VALUES (?,0,0) ON CONFLICT DO NOTHING")){
        p.setString(1,key);p.executeUpdate();}
      int generation;long expires;long dbNow;boolean first=false;
      try(PreparedStatement p=c.prepareStatement("SELECT generation,expires_at_ms,(EXTRACT(epoch FROM clock_timestamp())*1000)::BIGINT FROM p15_provider_index WHERE idem_key=? FOR UPDATE")){
        p.setString(1,key);try(ResultSet rs=p.executeQuery()){rs.next();generation=rs.getInt(1);expires=rs.getLong(2);dbNow=rs.getLong(3);}}
      if(expires<=dbNow){first=true;generation++;
        try(PreparedStatement p=c.prepareStatement("UPDATE p15_provider_index SET generation=?,expires_at_ms=? WHERE idem_key=?")){
          p.setInt(1,generation);p.setLong(2,dbNow+retention);p.setString(3,key);p.executeUpdate();}
        try(PreparedStatement p=c.prepareStatement("INSERT INTO p15_provider_effect(idem_key,generation,outcome,created_ms) VALUES (?,?,?,?)")){
          p.setString(1,key);p.setInt(2,generation);p.setString(3,"reject".equals(mode)?"REJECTED":"ACTIVATED");p.setLong(4,dbNow);p.executeUpdate();}
      }
      String actual;
      try(PreparedStatement p=c.prepareStatement("SELECT outcome FROM p15_provider_effect WHERE idem_key=? AND generation=?")){
        p.setString(1,key);p.setInt(2,generation);try(ResultSet rs=p.executeQuery()){rs.next();actual=rs.getString(1);}}
      c.commit(); // durable provider effect exists before any HTTP reply
      switch(mode){
        case "always503" -> answer(ex,503,null);
        case "after503", "after500" -> {if(first)answer(ex,503,null);else answer(ex,200,"MOCK:"+actual+":"+key);}
        case "badreceipt" -> answer(ex,200,"MOCK:ACTIVATED:WRONG_KEY");
        case "unknown599" -> answer(ex,599,"MOCK:"+actual+":"+key);
        case "noReceipt204" -> answer(ex,204,null);
        default -> answer(ex,"REJECTED".equals(actual)?422:200,"MOCK:"+actual+":"+key);
      }
    }catch(Exception exn){exn.printStackTrace(System.err);answer(ex,500,null);}
  }
  public static void main(String[] args)throws Exception {
    Class.forName("org.postgresql.Driver");setup();
    HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",Integer.parseInt(System.getenv().getOrDefault("P17_PROVIDER_PORT","18582"))),64);
    server.createContext("/activate",P17FiniteProvider::handle);
    server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());server.start();
    System.out.println("P17_PROVIDER_READY retention="+retention);System.out.flush();
  }
}
