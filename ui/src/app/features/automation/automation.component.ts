import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { OcrBlock, UiNode } from '../../core/models';

/**
 * Automation page — REQ-UI-11.
 *
 * Layout:
 *
 * - Left pane: window list. Authorize / Revoke per row.
 * - Centre: UI tree viewer. Collapsible nodes with bounded box +
 *   search box (text / automationId / controlType AND-semantics).
 * - Right: OCR preview. Run OCR over the active window, render the
 *   blocks with confidence + a redacted ``full_text`` summary.
 * - Bottom: screenshot capture + assert OCR contains button.
 *
 * The page is read-only + manual; the agent drives the heavy lifting
 * via MCP tools, this page exists for the operator to authorise
 * windows, browse the tree, and capture proof.
 */
@Component({
  selector: 'app-automation',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <h1>UI automation</h1>
    <div class="layout">
      <!-- Column 1: Windows -->
      <aside class="card">
        <h2>Windows</h2>
        @if (windows().length === 0) {
          <p class="lmcp-muted">no visible windows (or credential windows filtered)</p>
        } @else {
          <ul class="window-list">
            @for (w of windows(); track w.hwnd) {
              <li [class.active]="w.hwnd === activeHwnd()">
                <div class="lmcp-row">
                  <strong>{{ w.title || '(untitled)' }}</strong>
                  <span class="lmcp-spacer"></span>
                  <code>{{ w.process || 'unknown' }}</code>
                </div>
                <div class="lmcp-muted">pid {{ w.pid }} · hwnd {{ w.hwnd }}</div>
                @if (w.hwnd === activeHwnd() && authorizedId()) {
                  <button (click)="revoke()">Revoke</button>
                } @else {
                  <button (click)="authorize(w.hwnd, w.title, w.process, w.pid)">Authorize</button>
                }
              </li>
            }
          </ul>
        }
        <button (click)="refresh()">refresh</button>
      </aside>

      <!-- Column 2: UI tree -->
      <section class="card">
        <h2>UI tree</h2>
        @if (!authorizedId()) {
          <p class="lmcp-muted">authorise a window to read its tree</p>
        } @else {
          <div class="lmcp-row" style="margin-bottom: 8px;">
            <button (click)="loadTree()">load tree</button>
            <span class="lmcp-muted">
              @if (tree(); as t) {
                {{ t.total }} nodes · {{ t.truncated ? 'spilled to artifact ' + t.handle : 'inline' }}
              }
            </span>
          </div>
          <div class="find-row">
            <input [(ngModel)]="findText" placeholder="text contains…">
            <input [(ngModel)]="findAutomationId" placeholder="automationId exact">
            <input [(ngModel)]="findControlType" placeholder="controlType exact">
            <button (click)="find()">find</button>
          </div>
          @if (matches().length > 0) {
            <p class="lmcp-muted">{{ matches().length }} matches; top score: {{ matches()[0].score }}</p>
            <ul class="match-list">
              @for (m of matches(); track m.automationId + m.boundingBox.x + m.boundingBox.y) {
                <li>
                  <code>{{ m.controlType }}</code>
                  <strong>{{ m.name || '(no name)' }}</strong>
                  <span class="lmcp-muted">auto={{ m.automationId }} · score {{ m.score }}</span>
                </li>
              }
            </ul>
          }
          @if (tree()) {
            <div class="tree">
              @for (node of tree()!.summary; track node.automationId + node.boundingBox.x + node.boundingBox.y) {
                <details>
                  <summary>
                    <code>{{ node.controlType }}</code>
                    <strong>{{ node.name || '(no name)' }}</strong>
                    <span class="lmcp-muted">auto={{ node.automationId || '∅' }}</span>
                  </summary>
                  <pre>{{ formatNode(node) }}</pre>
                </details>
              }
            </div>
          }
        }
      </section>

      <!-- Column 3: OCR preview -->
      <section class="card">
        <h2>OCR</h2>
        @if (!authorizedId()) {
          <p class="lmcp-muted">authorise a window to OCR it</p>
        } @else {
          <div class="lmcp-row" style="margin-bottom: 8px;">
            <button (click)="runOcr()">run OCR</button>
          </div>
          @if (ocrResult(); as ocr) {
            @if (ocr.uncertain) {
              <p class="lmcp-error">uncertain — provider could not classify; results below are best-effort</p>
            } @else {
              <p class="lmcp-muted">{{ ocr.blocks.length }} blocks · handle {{ ocr.source_handle }}</p>
            }
            @for (b of ocr.blocks; track b.text + b.bounding_box.x + b.bounding_box.y) {
              <div class="ocr-block">
                <code>{{ b.confidence.toFixed(2) }}</code>
                <span>{{ b.text }}</span>
                <span class="lmcp-muted">({{ b.bounding_box.x }},{{ b.bounding_box.y }})</span>
              </div>
            }
            @if (ocr.full_text) {
              <details>
                <summary>full text (redacted)</summary>
                <pre>{{ ocr.full_text }}</pre>
              </details>
            }
          }
        }
      </section>
    </div>

    <!-- Bottom: screenshot + assert -->
    <section class="card" style="margin-top: 16px;">
      <h2>Capture</h2>
      @if (!authorizedId()) {
        <p class="lmcp-muted">authorise a window to capture it</p>
      } @else {
        <div class="lmcp-row">
          <button (click)="captureWindow()">screenshot</button>
          <input [(ngModel)]="assertText" placeholder="assert OCR contains…">
          <button (click)="assertOcr()">assert</button>
        </div>
        @if (lastScreenshot()) {
          <p class="lmcp-muted">handle: <code>{{ lastScreenshot() }}</code></p>
        }
        @if (assertResult(); as r) {
          <p [class.lmcp-error]="!r.passed">
            {{ r.passed ? 'PASS' : 'FAIL' }} · confidence {{ r.min_confidence | number: '1.2-2' }}
            · uncertain {{ r.uncertain }}
          </p>
        }
      }
    </section>

    @if (error()) {
      <p class="lmcp-error">{{ error() }}</p>
    }
  `,
  styles: [`
    .layout { display: grid; grid-template-columns: 320px 1fr 360px; gap: 16px; }
    .card { background: white; border: 1px solid #e0e0e0; border-radius: 8px;
      padding: 16px; }
    .window-list, .match-list { list-style: none; padding: 0; margin: 0 0 12px; }
    .window-list li { padding: 8px; border-radius: 4px; cursor: pointer;
      border: 1px solid transparent; }
    .window-list li.active { background: #e3f2fd; border-color: #1976d2; }
    .find-row { display: flex; gap: 6px; margin-bottom: 8px; }
    .find-row input { flex: 1; padding: 4px 8px; border: 1px solid #ccc;
      border-radius: 4px; }
    .tree { max-height: 480px; overflow: auto; padding: 8px;
      background: #fafafa; border-radius: 4px; }
    .tree summary { cursor: pointer; padding: 2px 0; }
    .tree pre { margin: 0 0 0 16px; font-size: 11px; color: #555; }
    .ocr-block { display: flex; gap: 8px; padding: 4px 0;
      border-bottom: 1px solid #eee; }
    code { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
    button { padding: 4px 12px; border: 1px solid #ccc; border-radius: 4px;
      background: white; cursor: pointer; }
    button:hover { background: #f5f5f5; }
    input { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px;
      flex: 1; }
    pre { white-space: pre-wrap; word-break: break-word; }
  `],
})
export class AutomationComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly windows = signal<Array<{ hwnd: number; title: string; process: string; pid: number }>>([]);
  readonly activeHwnd = signal<number | null>(null);
  readonly authorizedId = signal<string | null>(null);
  readonly lastScreenshot = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  // --- UI tree state ---
  readonly tree = signal<{
    nodes: UiNode[];
    truncated: boolean;
    handle: string | null;
    total: number;
    summary: UiNode[];
  } | null>(null);
  readonly matches = signal<Array<{
    name: string;
    automationId: string;
    controlType: string;
    boundingBox: { x: number; y: number; width: number; height: number };
    score: number;
  }>>([]);
  findText = '';
  findAutomationId = '';
  findControlType = '';

  // --- OCR state ---
  readonly ocrResult = signal<{
    blocks: OcrBlock[];
    full_text: string;
    uncertain: boolean;
    source_handle: string | null;
  } | null>(null);
  assertText = '';
  readonly assertResult = signal<{
    passed: boolean;
    min_confidence: number | null;
    uncertain: boolean;
  } | null>(null);

  async ngOnInit(): Promise<void> {
    await this.refresh();
  }

  async refresh(): Promise<void> {
    try {
      const data = await this.api.windowsList();
      this.windows.set(data.windows || []);
      this.error.set(null);
    } catch (e) {
      this.error.set(`failed to list windows: ${(e as Error).message}`);
    }
  }

  async authorize(hwnd: number, title: string, process: string, pid: number): Promise<void> {
    try {
      const result = await this.api.windowsAuthorize({ hwnd, title, process, pid });
      this.authorizedId.set(result.window.id);
      this.activeHwnd.set(hwnd);
      this.tree.set(null);
      this.matches.set([]);
      this.ocrResult.set(null);
      this.error.set(null);
    } catch (e) {
      this.error.set(`authorize failed: ${(e as Error).message}`);
    }
  }

  async revoke(): Promise<void> {
    const id = this.authorizedId();
    if (!id) return;
    try {
      await this.api.windowsRevoke(id);
      this.authorizedId.set(null);
      this.activeHwnd.set(null);
      this.lastScreenshot.set(null);
      this.tree.set(null);
      this.matches.set([]);
      this.ocrResult.set(null);
      this.error.set(null);
    } catch (e) {
      this.error.set(`revoke failed: ${(e as Error).message}`);
    }
  }

  async loadTree(): Promise<void> {
    const id = this.authorizedId();
    if (!id) return;
    try {
      const result = await this.api.uiGetTree(id, 4);
      this.tree.set(result);
      this.error.set(null);
    } catch (e) {
      this.error.set(`tree load failed: ${(e as Error).message}`);
    }
  }

  async find(): Promise<void> {
    const id = this.authorizedId();
    if (!id) return;
    const body: {
      window_id: string;
      text?: string;
      automationId?: string;
      controlType?: string;
    } = { window_id: id };
    if (this.findText) body.text = this.findText;
    if (this.findAutomationId) body.automationId = this.findAutomationId;
    if (this.findControlType) body.controlType = this.findControlType;
    try {
      const result = await this.api.uiFindElement(body);
      this.matches.set(result.matches || []);
      this.error.set(null);
    } catch (e) {
      this.error.set(`find failed: ${(e as Error).message}`);
    }
  }

  async captureWindow(): Promise<void> {
    const id = this.authorizedId();
    if (!id) return;
    try {
      const result = await this.api.uiScreenshotWindow(id);
      const data = result as { handle?: string; error?: { code: string; message: string } };
      if (data.error) {
        this.error.set(`screenshot failed: ${data.error.message}`);
        return;
      }
      this.lastScreenshot.set(data.handle ?? null);
      this.error.set(null);
    } catch (e) {
      this.error.set(`screenshot failed: ${(e as Error).message}`);
    }
  }

  async runOcr(): Promise<void> {
    const id = this.authorizedId();
    if (!id) return;
    try {
      const result = await this.api.ocrRegion({ window_id: id });
      const data = result as {
        blocks?: OcrBlock[];
        full_text?: string;
        uncertain?: boolean;
        source_handle?: string | null;
        error?: { code: string; message: string };
      };
      if (data.error) {
        this.error.set(`OCR failed: ${data.error.message}`);
        return;
      }
      this.ocrResult.set({
        blocks: data.blocks || [],
        full_text: data.full_text || '',
        uncertain: !!data.uncertain,
        source_handle: data.source_handle ?? null,
      });
      this.error.set(null);
    } catch (e) {
      this.error.set(`OCR failed: ${(e as Error).message}`);
    }
  }

  async assertOcr(): Promise<void> {
    const id = this.authorizedId();
    if (!id || !this.assertText) return;
    try {
      const result = await this.api.ocrAssertText({
        window_id: id,
        expected: this.assertText,
      });
      this.assertResult.set({
        passed: !!result.passed,
        min_confidence: result.min_confidence,
        uncertain: !!result.uncertain,
      });
      this.error.set(null);
    } catch (e) {
      this.error.set(`assert failed: ${(e as Error).message}`);
    }
  }

  formatNode(node: UiNode): string {
    return `name=${node.name || '∅'}  type=${node.controlType}  ` +
      `auto=${node.automationId || '∅'}  enabled=${node.isEnabled}  ` +
      `visible=${node.isVisible}  bbox=${node.boundingBox.x},${node.boundingBox.y}` +
      ` ${node.boundingBox.width}x${node.boundingBox.height}`;
  }
}