// TEST ONLY - not an implementation of Spring.
package org.springframework.context.annotation;
@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.RUNTIME)
public @interface Bean {String initMethod() default "";String destroyMethod() default "";}
