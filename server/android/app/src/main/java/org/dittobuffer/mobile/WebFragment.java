// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

package org.dittobuffer.mobile;

import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

/** Embedded localhost browser (admin console). JS + DOM storage enabled. */
public final class WebFragment extends Fragment {
    private static final String ARG_URL = "url";
    private static final String ARG_TITLE = "title";
    private static final String RETRY = "\n\nTap here to retry.";
    private static final long START_TIMEOUT_MS = 90_000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable waitForServer = new Runnable() {
        @Override public void run() {
            if (web == null) return;
            if (ServerRuntime.isRunning()) {
                waiting.setVisibility(View.GONE);
                loadHome();
                return;
            }
            if (!ServerRuntime.lastError().isEmpty()) {
                showError("The local server stopped while starting.\nTap here to try again.");
                return;
            }
            if (SystemClock.elapsedRealtime() - waitStarted > START_TIMEOUT_MS) {
                waiting.setVisibility(View.GONE);
                showError("The local server is not running yet.\nStart it from Home, then tap here.");
                return;
            }
            waiting.setText("Starting the local server…\nThe first start can take a minute.");
            handler.postDelayed(this, 750);
        }
    };

    private WebView web;
    private ProgressBar progress;
    private TextView error;
    private TextView waiting;
    private String homeUrl;
    private boolean loaded;
    private boolean failed;
    private long waitStarted;

    public static WebFragment newInstance(String url, String title) {
        WebFragment fragment = new WebFragment();
        Bundle args = new Bundle();
        args.putString(ARG_URL, url);
        args.putString(ARG_TITLE, title);
        fragment.setArguments(args);
        return fragment;
    }

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View view = inflater.inflate(R.layout.fragment_web, container, false);
        web = view.findViewById(R.id.web_view);
        progress = view.findViewById(R.id.web_progress);
        error = view.findViewById(R.id.web_error);
        waiting = view.findViewById(R.id.web_waiting);
        error.setOnClickListener(v -> retry());
        waiting.setOnClickListener(v -> retry());

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                failed = false;
                error.setVisibility(View.GONE);
                progress.setVisibility(View.VISIBLE);
            }

            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                if (!failed) error.setVisibility(View.GONE);
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                                  WebResourceError error) {
                if (request.isForMainFrame()) {
                    showError("The local server is not ready yet.\nStart it from Home, then tap here.");
                }
            }

            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                                      WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 500) {
                    showError("The local admin is still starting. Tap here to try again.");
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int newProgress) {
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
                progress.setProgress(newProgress);
            }
        });
        homeUrl = getArguments() == null ? "http://127.0.0.1:8080/"
                : getArguments().getString(ARG_URL, "http://127.0.0.1:8080/");
        if (ServerRuntime.isRunning()) loadHome();
        else beginWait();
        return view;
    }

    private void loadHome() {
        if (loaded || web == null) return;
        loaded = true;
        waiting.setVisibility(View.GONE);
        error.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(homeUrl);
    }

    private void beginWait() {
        loaded = false;
        failed = false;
        error.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        waiting.setVisibility(View.VISIBLE);
        waiting.setText("Starting the local server…");
        waitStarted = SystemClock.elapsedRealtime();
        handler.removeCallbacks(waitForServer);
        handler.post(waitForServer);
    }

    private void showError(String message) {
        failed = true;
        progress.setVisibility(View.GONE);
        waiting.setVisibility(View.GONE);
        error.setVisibility(View.VISIBLE);
        error.setText(message + RETRY);
    }

    private void retry() {
        loaded = false;
        if (ServerRuntime.isRunning()) {
            if (web != null && web.getUrl() != null) web.reload();
            else loadHome();
        } else {
            beginWait();
        }
    }

    public boolean canGoBack() {
        return web != null && web.canGoBack();
    }

    public void goBack() {
        if (web != null) web.goBack();
    }

    @Override
    public void onDestroyView() {
        handler.removeCallbacks(waitForServer);
        if (web != null) {
            web.stopLoading();
            web.destroy();
            web = null;
        }
        super.onDestroyView();
    }
}
