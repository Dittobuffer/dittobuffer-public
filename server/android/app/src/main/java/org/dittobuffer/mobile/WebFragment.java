package org.dittobuffer.mobile;

import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
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

    private WebView web;
    private ProgressBar progress;
    private TextView error;

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

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                error.setVisibility(View.GONE);
            }
            @Override public void onReceivedError(WebView view, int code, String description, String failingUrl) {
                progress.setVisibility(View.GONE);
                error.setVisibility(View.VISIBLE);
                error.setText("Cannot reach " + failingUrl + "\n" + description
                        + "\nStart the server on the Home tab.");
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int newProgress) {
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
                progress.setProgress(newProgress);
            }
        });
        String url = getArguments() == null ? "http://127.0.0.1:8080/"
                : getArguments().getString(ARG_URL, "http://127.0.0.1:8080/");
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(url);
        return view;
    }

    @Override
    public void onDestroyView() {
        if (web != null) {
            web.stopLoading();
            web.destroy();
            web = null;
        }
        super.onDestroyView();
    }
}
