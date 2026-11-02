package demo;

import com.rabbitmq.client.*;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.*;

/** RabbitMQ push/ACK, durable outbox publication and consumer lifecycle.
 * Business state, authorization and provider HTTP remain outside this class.
 */
final class RabbitDelivery implements AutoCloseable {
  @FunctionalInterface interface WorkHandler {void handle(String requestId) throws Exception;}
  @FunctionalInterface interface Clock {long now() throws SQLException;}

  private final JdbcRabbitLedger ledger;
  private final String queue;
  private final WorkHandler work;
  private final Clock clock;
  private final ConnectionFactory factory=new ConnectionFactory();
  private final List<Channel> consumers=new CopyOnWriteArrayList<>();
  private final AtomicInteger published=new AtomicInteger();
  private final AtomicBoolean dropAckOnce=new AtomicBoolean("1".equals(System.getenv("P13_DROP_ACK_ONCE")));
  private final AtomicBoolean dropConfirmOnce=new AtomicBoolean("1".equals(System.getenv("P13_DROP_CONFIRM_ONCE")));
  private volatile com.rabbitmq.client.Connection broker;

  RabbitDelivery(JdbcRabbitLedger ledger,String queue,WorkHandler work,Clock clock) {
    this.ledger=ledger;this.queue=queue;this.work=work;this.clock=clock;
    // Configurable for containerized/remote test brokers; retain local lab defaults.
    String host=System.getenv().getOrDefault("P13_RABBIT_HOST","127.0.0.1");
    String port=System.getenv().getOrDefault("P13_RABBIT_PORT","5672");
    String user=System.getenv().getOrDefault("P13_RABBIT_USER","guest");
    String pass=System.getenv().getOrDefault("P13_RABBIT_PASSWORD","guest");
    if(host.isBlank()||user.isBlank()||pass.isBlank())throw new IllegalArgumentException("Rabbit connection settings must not be blank");
    int portNumber=Integer.parseInt(port);
    if(portNumber<1||portNumber>65535)throw new IllegalArgumentException("Rabbit port out of range");
    factory.setHost(host);factory.setPort(portNumber);
    factory.setUsername(user);factory.setPassword(pass);
    factory.setAutomaticRecoveryEnabled(true);
    factory.setTopologyRecoveryEnabled(true);
    factory.setNetworkRecoveryInterval(1000);
  }
  int publishedCount(){return published.get();}
  boolean isOpen(){var connection=broker;return connection!=null&&connection.isOpen();}
  void start(String instance) throws Exception {
    broker=factory.newConnection("p13-"+instance);
    try(Channel setup=broker.createChannel()){
      setup.queueDeclare(queue,true,false,false,Map.of("x-queue-type","quorum"));
    }
    for(int i=0;i<2;i++){
      Channel ch=broker.createChannel();consumers.add(ch);consumer(ch);
    }
  }
  List<String[]> dueOutbox(long ms)throws SQLException{
    List<String[]> rows=new ArrayList<>();
    try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement("SELECT nid,rid FROM p9_outbox WHERE published_ms IS NULL AND due_ms<=? AND token_until<=? ORDER BY due_ms,nid LIMIT 16")){
      p.setLong(1,ms);p.setLong(2,ms);try(ResultSet r=p.executeQuery()){while(r.next())rows.add(new String[]{r.getString(1),r.getString(2)});}
    }return rows;
  }
  void dispatcher(){
    try {
      long ms=clock.now();List<String[]> rows=dueOutbox(ms);if(rows.isEmpty())return;
      // Publisher uses its own channel, one publish at a time. Mandatory returns observed before confirm.
      try(Channel ch=broker.createChannel()){
        ch.confirmSelect();
        for(String[] row:rows){
          String nid=row[0],rid=row[1],token=UUID.randomUUID().toString();
          long start=clock.now();long until=start+8000;
          var res=ledger.reserveWithEpoch(nid,token,start,until);
          if(res.outcome()!=JdbcRabbitLedger.Outcome.COMMITTED)continue;
          AtomicBoolean returned=new AtomicBoolean();
          ReturnListener callback=ch.addReturnListener((ReturnCallback) msg->returned.set(true));
          try{
            ch.basicPublish("",queue,true,new AMQP.BasicProperties.Builder().deliveryMode(2).messageId(nid).contentType("text/plain").build(),rid.getBytes(StandardCharsets.UTF_8));
            ch.waitForConfirmsOrDie(4000);
            if(!returned.get()){
              published.incrementAndGet();
              if(dropConfirmOnce.compareAndSet(true,false)){
                System.out.println("P13_FAULT published but deliberately forgot broker confirm nid="+nid);
              }else{
                var result=ledger.markPublished(nid,token,res.epoch(),clock.now());
                if(result!=JdbcRabbitLedger.Outcome.COMMITTED)System.err.println("P13_PUBLISH_MARK " + result+" nid="+nid);
              }
            }else System.err.println("P13_UNROUTABLE nid="+nid);
          }finally{ch.removeReturnListener(callback);}
        }
      }
    }catch(Exception ex){System.err.println("P13_DISPATCHER_ERROR "+ex);}
  }
  void restoreConsumers(){
    try {
      if(broker==null || !broker.isOpen())return;
      for(Channel old:new ArrayList<>(consumers)){
        if(!old.isOpen()){
          consumers.remove(old);
          Channel fresh=broker.createChannel();consumers.add(fresh);consumer(fresh);
          System.out.println("P14_CONSUMER_RESTORED");
        }
      }
    }catch(Exception e){System.err.println("P14_CONSUMER_RESTORE_ERROR "+e);}
  }
  void consumer(Channel channel){
    try{
      channel.basicQos(4);
      channel.basicConsume(queue,false,(tag,msg)->{
        long dtag=msg.getEnvelope().getDeliveryTag();
        String rid=new String(msg.getBody(),StandardCharsets.UTF_8);
        try {
          work.handle(rid);
          if(dropAckOnce.compareAndSet(true,false)){
            System.out.println("P13_FAULT settled but dropped ACK rid="+rid);
            channel.abort(); // forcibly release unacked message for redelivery on another consumer
          }else if(channel.isOpen())channel.basicAck(dtag,false);
        }catch(Throwable e){
          System.err.println("P13_CONSUMER_ERROR rid="+rid+" error="+e);
          // Do not ACK uncertain state. Closing channel requeues; watchdog remains durable fallback.
          try{channel.abort();}catch(Exception ignored){}
        }
      },tag->{});
    }catch(Exception e){throw new RuntimeException(e);}
  }
  @Override public void close(){
    for(Channel channel:consumers){try{channel.close();}catch(Exception ignored){}}
    consumers.clear();
    com.rabbitmq.client.Connection connection=broker;
    broker=null;
    if(connection!=null){try{connection.close();}catch(Exception ignored){}}
  }
}
