package com.b7proxtrading.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.*
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView

    companion object {
        private const val PERMISSION_REQUEST_CODE = 101
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)

        requestNeededPermissions()
        setupWebView()
        startTradingForegroundService()
        initPythonAndLoad()
    }

    private fun requestNeededPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    PERMISSION_REQUEST_CODE
                )
            }
        }
    }

    private fun startTradingForegroundService() {
        val serviceIntent = Intent(this, TradingService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.allowFileAccess = true
        settings.allowContentAccess = true
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.setSupportZoom(true)
        settings.builtInZoomControls = true
        settings.displayZoomControls = false
        settings.cacheMode = WebSettings.LOAD_DEFAULT

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                progressBar.visibility = View.GONE
                statusText.visibility = View.GONE
                webView.visibility = View.VISIBLE
            }

            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                super.onReceivedError(view, request, error)
            }
        }

        webView.webChromeClient = WebChromeClient()
    }

    private fun initPythonAndLoad() {
        lifecycleScope.launch(Dispatchers.IO) {
            withContext(Dispatchers.Main) {
                statusText.text = "Menginisialisasi modul Python..."
            }

            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(applicationContext))
            }

            val py = Python.getInstance()
            val filesDir = filesDir.absolutePath

            withContext(Dispatchers.Main) {
                statusText.text = "Menjalankan engine B7PROX..."
            }

            val entryModule = py.getModule("entrypoint_android")
            entryModule.callAttr("start_engine", filesDir)

            withContext(Dispatchers.Main) {
                statusText.text = "Menghubungkan ke dashboard trading..."
            }

            // Loop polling hingga web server localhost:8080 aktif
            var serverReady = false
            for (i in 1..40) {
                delay(600)
                try {
                    val url = URL("http://127.0.0.1:8080")
                    val conn = url.openConnection() as HttpURLConnection
                    conn.connectTimeout = 600
                    conn.readTimeout = 600
                    conn.requestMethod = "GET"
                    val code = conn.responseCode
                    if (code in 200..399) {
                        serverReady = true
                        conn.disconnect()
                        break
                    }
                    conn.disconnect()
                } catch (_: Exception) {
                    // Masih loading
                }
            }

            withContext(Dispatchers.Main) {
                if (serverReady) {
                    webView.loadUrl("http://127.0.0.1:8080")
                } else {
                    progressBar.visibility = View.GONE
                    statusText.text = "Gagal memuat engine trading lokal.\nSilakan tutup & buka kembali aplikasi."
                }
            }
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            // Biarkan tombol back meminimize aplikasi alih-alih mematikan service
            moveTaskToBack(true)
        }
    }
}
