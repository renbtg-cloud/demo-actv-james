package demo;

/** Integrated standalone application bootstrap.
 * Maintains the original engine implementation under P13Service until
 * its production-oriented refactoring and Spring Boot adapter are delivered.
 */
public final class DeroyqueApplication {
  private DeroyqueApplication() {}
  public static void main(String[] args) throws Exception {
    new P13Service().start();
  }
}
