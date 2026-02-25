package com.octopilot.integration.cronjobapp

import org.springframework.boot.CommandLineRunner
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication

@SpringBootApplication
class CronjobApp

fun main(args: Array<String>) {
    runApplication<CronjobApp>(*args)
}

@org.springframework.context.annotation.Bean
fun runner(): CommandLineRunner = CommandLineRunner {
    val durationMinutes = 3
    val totalMillis = durationMinutes * 60 * 1000L
    val chunkMillis = 10_000L // log every 10s
    var elapsed = 0L
    println("Cronjob-app: running for $durationMinutes minutes then exiting...")
    while (elapsed < totalMillis) {
        Thread.sleep(chunkMillis.coerceAtMost(totalMillis - elapsed))
        elapsed += chunkMillis
        println("Cronjob-app: running... ${elapsed / 1000}s elapsed")
    }
    println("Cronjob-app: done after $durationMinutes minutes, exiting 0")
    kotlin.system.exitProcess(0)
}
