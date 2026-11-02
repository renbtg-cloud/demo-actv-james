// TEST ONLY - not an implementation of Spring.
package org.springframework.boot.autoconfigure.condition;
public @interface ConditionalOnProperty {String name();String havingValue() default "";}
