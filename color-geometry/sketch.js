/**
 * 2026 流行色幾何互動藝術空間
 * 核心繪圖與互動邏輯
 * 
 * 符合程式規範：包含函式級別中文註解，重要變數加註
 */

// ============================================================
// 1. 全域變數定義
// ============================================================

// 2026 年流行色調色盤數據庫
const COLOR_PALETTES = [
  {
    name: "變革之境",
    // 變革藍綠、雲霧白、冰川藍、柑橘黃
    colors: ["#005A5B", "#F2F0EA", "#A4D8F4", "#E7FE00"]
  },
  {
    name: "奢華陶土",
    // 陶土色、煙燻紫、暖心粉、雲霧白
    colors: ["#B85A3C", "#604A76", "#FCAEBB", "#F2F0EA"]
  },
  {
    name: "清新衝擊",
    // 酸橙綠、番茄紅、深棕色、冰川藍
    colors: ["#D4FF3A", "#FF3B30", "#723D2E", "#A4D8F4"]
  },
  {
    name: "極簡冷靜",
    // 冰川藍、煙燻紫、雲霧白、變革藍綠
    colors: ["#A4D8F4", "#604A76", "#F2F0EA", "#005A5B"]
  }
];

// 當前運行的設定狀態
let currentPaletteIndex = 0;   // 當前調色盤索引
let targetPaletteColors = [];  // 目標色彩組 (用於 lerpColor 平滑過渡)
let interpolatedColors = [];   // 當前渲染色彩組 (漸變中)
let visualMode = 1;            // 當前視覺模式 (1: 有機流動, 2: 包浩斯秩序, 3: 弦音波紋)
let autoFlow = true;           // 是否開啟自動流動
let noiseScale = 0.005;        // Perlin Noise 的縮放比例
let globalTime = 0;            // 全域時間計數器

// 控制面板參數 (由 DOM 滑桿同步)
let densityVal = 12;           // 圖形密度
let speedVal = 1.0;            // 流速倍率
let sensitivityVal = 1.0;      // 滑鼠互動感應度

// 互動元素系統
let particles = [];            // 滑鼠軌跡粒子陣列
let rippleWave = null;         // 點擊滑鼠產生的波紋物件

// ============================================================
// 2. p5.js 生命週期函數
// ============================================================

/**
 * 畫布初始化設置
 */
function setup() {
  const canvas = createCanvas(windowWidth, windowHeight);
  canvas.parent("canvas-container");
  
  // 設置色彩模式為 HSB 或 RGB，此處使用 RGB 以進行精準流行色色彩插值
  colorMode(RGB, 255);
  
  // 初始化調色盤顏色
  initializeColors(currentPaletteIndex);
  
  
  // 綁定 HTML DOM 控制面板的事件
  bindInterfaceEvents();
  
  // 渲染 initial 色譜預覽
  renderPalettePreview();
}

/**
 * 每一幀的畫面渲染與邏輯更新
 */
function draw() {
  // 1. 色彩平滑過渡 (當前色向目標色逼近)
  updateColorInterpolation();
  
  // 2. 獲取當前滑鼠位置驅動的雙線性插值底色
  let baseBg = getInteractiveColor(mouseX, mouseY);
  
  // 以微弱的透明度繪製背景，在某些模式下產生殘影與流動感
  if (visualMode === 3) {
    background(red(baseBg), green(baseBg), blue(baseBg), 35);
  } else {
    background(red(baseBg), green(baseBg), blue(baseBg), 255);
  }
  
  // 3. 更新全域時間
  if (autoFlow) {
    globalTime += 0.01 * speedVal;
  }
  
  // 4. 更新並渲染點擊波紋
  if (rippleWave) {
    rippleWave.update();
    rippleWave.display();
    if (rippleWave.isDead()) {
      rippleWave = null;
    }
  }
  
  // 5. 渲染視覺模式
  push();
  if (visualMode === 1) {
    drawOrganicFlow();
  } else if (visualMode === 2) {
    drawBauhausGrid();
  } else if (visualMode === 3) {
    drawBezierRipples();
  }
  pop();
  
  // 6. 更新並渲染粒子系統 (滑鼠軌跡)
  updateAndDrawParticles();
  
}

/**
 * 視窗大小改變時重設畫布
 */
function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
}

/**
 * 滑鼠移動時觸發：產生軌跡粒子
 */
function mouseMoved() {
  // 限制粒子生成頻率，並在滑鼠移入畫布時產生
  if (mouseX > 0 && mouseX < width && mouseY > 0 && mouseY < height) {
    // 根據移動速度決定粒子數量
    let speed = dist(mouseX, mouseY, pmouseX, pmouseY);
    // 只有在滑鼠有顯著移動時才產生粒子，且進行機率節流
    if (speed > 4 && random() < 0.3 * sensitivityVal) {
      particles.push(new ArtParticle(mouseX, mouseY));
      
      // 強制上限，避免高回報率滑鼠/拖曳造成粒子數爆炸，導致 CPU 與 GPU 繪圖指令隊列塞爆卡死
      if (particles.length > 60) {
        particles.shift();
      }
    }
  }
}

/**
 * 滑鼠拖曳時觸發：與移動滑鼠同步產生粒子
 */
function mouseDragged() {
  mouseMoved();
}

/**
 * 滑鼠點擊時觸發：產生漣漪波紋並重組或過渡顏色
 */
function mousePressed() {
  // 避免點擊控制面板時觸發畫布波紋 (面板寬度約為 380px + 邊距)
  if (mouseX < 420 && mouseY < height - 30 && mouseX > 0 && mouseY > 0) {
    return;
  }
  
  // 在點擊處觸發波紋
  rippleWave = new Ripple(mouseX, mouseY);
  
  // 隨機在當前色譜中抖動顏色，產生視覺衝擊
  for (let i = 0; i < targetPaletteColors.length; i++) {
    // 稍微混合隨機流行色，在滑鼠點擊時引起配色微變
    let randomPal = random(COLOR_PALETTES);
    let randColor = color(random(randomPal.colors));
    targetPaletteColors[i] = lerpColor(targetPaletteColors[i], randColor, 0.4);
  }
}

// ============================================================
// 3. 色彩控制與雙線性插值 (Bilinear Interpolation)
// ============================================================

/**
 * 初始化調色盤的初始色與目標色
 * @param {number} index 調色盤索引
 */
function initializeColors(index) {
  let palette = COLOR_PALETTES[index].colors;
  targetPaletteColors = [];
  interpolatedColors = [];
  
  for (let i = 0; i < palette.length; i++) {
    targetPaletteColors.push(color(palette[i]));
    // 初始時直接設為該顏色
    interpolatedColors.push(color(palette[i]));
  }
}

/**
 * 每一幀平滑更新當前顏色，趨近目標顏色
 */
function updateColorInterpolation() {
  const lerpSpeed = 0.08; // 漸變速度
  for (let i = 0; i < interpolatedColors.length; i++) {
    interpolatedColors[i] = lerpColor(interpolatedColors[i], targetPaletteColors[i], lerpSpeed);
  }
}

/**
 * 雙線性插值算法：根據滑鼠在畫布上的 x, y 比例，融合調色盤的 4 種顏色
 * @param {number} mx 滑鼠 X
 * @param {number} my 滑鼠 Y
 * @returns {p5.Color} 插值後的核心色彩
 */
function getInteractiveColor(mx, my) {
  // 歸一化座標
  let tx = constrain(mx / width, 0, 1);
  let ty = constrain(my / height, 0, 1);
  
  // 當前調色盤的四個端點色彩 (0: 左上, 1: 右上, 2: 左下, 3: 右下)
  let cTL = interpolatedColors[0];
  let cTR = interpolatedColors[2 % interpolatedColors.length]; // 防呆
  let cBL = interpolatedColors[1];
  let cBR = interpolatedColors[3 % interpolatedColors.length];
  
  // 在 X 方向上進行線性插值
  let cTop = lerpColor(cTL, cTR, tx);
  let cBottom = lerpColor(cBL, cBR, tx);
  
  // 在 Y 方向上再進行一次插值
  return lerpColor(cTop, cBottom, ty);
}

// ============================================================
// 4. 視覺模式繪製邏輯
// ============================================================

/**
 * 模式 1: 有機流動 (Generative Flow - Blobs)
 * 渲染多個相互重疊、帶有透明感的 Perlin Noise 變形蟲
 */
function drawOrganicFlow() {
  noStroke();
  
  // 疊加模式設置，創造高級的透光重疊感
  blendMode(BLEND);
  
  // 根據密度繪製不同數量的 Blob
  let count = densityVal;
  for (let i = 0; i < count; i++) {
    // 依序選用調色盤的顏色，加入透明度
    let baseColor = interpolatedColors[i % interpolatedColors.length];
    let c = color(red(baseColor), green(baseColor), blue(baseColor), 110 + sin(globalTime + i) * 30);
    fill(c);
    
    // 計算 Blob 的中心坐標
    let cx = width * (0.3 + 0.4 * noise(i * 10, globalTime * 0.2));
    let cy = height * (0.3 + 0.4 * noise(i * 10 + 5, globalTime * 0.2));
    
    // 基底半徑
    let baseRadius = min(width, height) * (0.15 + (i * 0.02));
    
    // 繪製 Blob 頂點
    push();
    translate(cx, cy);
    beginShape();
    
    let numPoints = 72; // 從 120 降至 72，維持平滑度同時減少 40% 的幾何運算開銷
    for (let a = 0; a < TWO_PI; a += TWO_PI / numPoints) {
      let xoff = cos(a) + 1;
      let yoff = sin(a) + 1;
      
      // 用 Perlin Noise 調變半徑，使形狀自然扭動
      let n = noise(xoff * 0.8 + i, yoff * 0.8 + globalTime * 0.5);
      let r = baseRadius * (0.75 + n * 0.5);
      
      // 滑鼠感應變形力：當滑鼠靠近形狀邊緣，產生局部形變
      let worldX = cx + cos(a) * r;
      let worldY = cy + sin(a) * r;
      let dToMouse = dist(mouseX, mouseY, worldX, worldY);
      
      if (dToMouse < 250 * sensitivityVal) {
        // 排斥力計算
        let force = map(dToMouse, 0, 250 * sensitivityVal, 40 * sensitivityVal, 0);
        r += force * sin(a * 4 + globalTime * 2);
      }
      
      // 波紋干涉
      if (rippleWave) {
        let dToRipple = dist(rippleWave.x, rippleWave.y, worldX, worldY);
        let ripForce = rippleWave.getForceAtDistance(dToRipple);
        r += ripForce * 35;
      }
      
      let x = cos(a) * r;
      let y = sin(a) * r;
      vertex(x, y);
    }
    endShape(CLOSE);
    pop();
  }
}

/**
 * 模式 2: 包浩斯秩序 (Bauhaus Grid)
 * 網格排列的動態幾何圖形，受到滑鼠引力而產生旋轉與顏色變化
 */
function drawBauhausGrid() {
  blendMode(BLEND);
  rectMode(CENTER);
  noStroke();
  
  // 計算行列數
  let cols = densityVal;
  let rows = ceil(cols * (height / width));
  
  let cellW = width / cols;
  let cellH = height / rows;
  
  for (let i = 0; i < cols; i++) {
    for (let j = 0; j < rows; j++) {
      let cx = i * cellW + cellW / 2;
      let cy = j * cellH + cellH / 2;
      
      // 計算與滑鼠的距離
      let d = dist(mouseX, mouseY, cx, cy);
      let maxDist = dist(0, 0, width, height) * 0.25 * sensitivityVal;
      
      // 根據距離計算影響權重 (0 ~ 1)
      let factor = map(d, 0, maxDist, 1, 0, true);
      
      // 根據 Noise 與滑鼠權重計算旋轉角度
      let angle = noise(i * 0.1, j * 0.1, globalTime * 0.3) * TWO_PI;
      angle += factor * PI * sensitivityVal;
      
      // 波紋偏移
      let ripOffset = 0;
      if (rippleWave) {
        let dToRipple = dist(rippleWave.x, rippleWave.y, cx, cy);
        ripOffset = rippleWave.getForceAtDistance(dToRipple) * 0.5;
        angle += ripOffset * PI;
      }
      
      // 配色選擇：混合網格基本流行色與滑鼠互動色
      let baseColor = interpolatedColors[(i + j) % interpolatedColors.length];
      let hoverColor = interpolatedColors[(i * 2) % interpolatedColors.length];
      let finalColor = lerpColor(baseColor, hoverColor, factor);
      
      push();
      translate(cx, cy);
      rotate(angle);
      
      // 隨機但穩定的幾何形狀分配 (圓形、三角形、雙重扇形、正方形)
      let shapeType = (i * 7 + j * 13) % 4;
      let size = min(cellW, cellH) * (0.6 + factor * 0.3);
      
      fill(red(finalColor), green(finalColor), blue(finalColor), 230);
      
      if (shapeType === 0) {
        // 正方形
        rect(0, 0, size, size, size * 0.15);
      } else if (shapeType === 1) {
        // 圓形
        circle(0, 0, size);
      } else if (shapeType === 2) {
        // 等腰三角形
        triangle(-size/2, size/2, 0, -size/2, size/2, size/2);
      } else if (shapeType === 3) {
        // 切片扇形 (呈現包浩斯經典弧形)
        arc(-size/2, -size/2, size*2, size*2, 0, HALF_PI);
      }
      
      // 點綴對比色小圓點，增加精緻感
      if (factor > 0.3) {
        let dotColor = interpolatedColors[(i + j + 1) % interpolatedColors.length];
        fill(dotColor);
        circle(0, 0, size * 0.2);
      }
      
      pop();
    }
  }
}

/**
 * 模式 3: 弦音波紋 (Wave Harmonics - Bézier Ripples)
 * 由流暢貝茲曲線束構成的線條流動，呈現迷幻且柔和的聲波藝術
 */
function drawBezierRipples() {
  blendMode(BLEND);
  noFill();
  
  let linesCount = densityVal * 2;
  
  for (let i = 0; i < linesCount; i++) {
    // 流行色線性漸變
    let baseColor = interpolatedColors[i % interpolatedColors.length];
    let strokeW = map(i, 0, linesCount, 1.5, 4);
    
    // 漸層透明度
    let alpha = map(sin(globalTime + i * 0.1), -1, 1, 100, 220);
    stroke(red(baseColor), green(baseColor), blue(baseColor), alpha);
    strokeWeight(strokeW);
    
    // 控制貝茲曲線的 4 個控制點
    // 起點、終點固定在畫布兩側，控制點隨 Noise 與滑鼠高度起伏
    let yAnchorOffset = map(i, 0, linesCount, 0.05, 0.95) * height;
    
    // 滑鼠與 Perlin noise 共同驅動控制點
    let noiseVal = noise(i * 0.05, globalTime * 0.15);
    
    let x1 = 0;
    let y1 = yAnchorOffset + sin(globalTime + i) * 20;
    
    // 控制點 1: 受滑鼠 X 坐標影響
    let cx1 = width * 0.25 + (mouseX - width/2) * 0.2 * sensitivityVal;
    let cy1 = yAnchorOffset + map(noiseVal, 0, 1, -250, 250) + (mouseY - height/2) * 0.5;
    
    // 控制點 2: 與控制點 1 呈相位差
    let cx2 = width * 0.75 - (mouseX - width/2) * 0.2 * sensitivityVal;
    let cy2 = yAnchorOffset + map(noise(i * 0.05 + 100, globalTime * 0.15), 0, 1, -250, 250) - (mouseY - height/2) * 0.5;
    
    let x2 = width;
    let y2 = yAnchorOffset - sin(globalTime + i) * 20;
    
    // 點擊波紋對曲線進行干涉
    if (rippleWave) {
      let waveForce = rippleWave.getForceAtDistance(width * 0.5);
      cy1 += waveForce * 80 * sin(i);
      cy2 -= waveForce * 80 * cos(i);
    }
    
    // 繪製貝茲曲線
    bezier(x1, y1, cx1, cy1, cx2, cy2, x2, y2);
    
    // 在貝茲曲線中間節點繪製裝飾性幾何粒子小點
    if (i % 3 === 0) {
      // 取得曲線中點 (t=0.5) 的坐標
      let mx = bezierPoint(x1, cx1, cx2, x2, 0.5);
      let my = bezierPoint(y1, cy1, cy2, y2, 0.5);
      fill(baseColor);
      noStroke();
      circle(mx, my, strokeW * 2.5);
      noFill();
    }
  }
}

// ============================================================
// 5. 互動特效系統 (粒系統 & 漣漪)
// ============================================================

/**
 * 軌跡粒子系統 (ArtParticle) 類別定義
 */
class ArtParticle {
  constructor(x, y) {
    this.x = x;
    this.y = y;
    // 賦予隨機初速度
    this.vx = random(-2, 2);
    this.vy = random(-2, 2);
    this.size = random(6, 16);
    this.angle = random(TWO_PI);
    this.rotSpeed = random(-0.05, 0.05);
    // 生命週期 (1.0 遞減至 0.0)
    this.life = 1.0;
    this.decay = random(0.015, 0.035);
    // 隨機選用流行色
    this.color = random(interpolatedColors);
    // 隨機形狀 (0: 矩形, 1: 三角形, 2: 十字)
    this.type = floor(random(3));
  }
  update() {
    this.x += this.vx;
    this.y += this.vy;
    this.angle += this.rotSpeed;
    this.life = max(0, this.life - this.decay);
  }

  display() {
    push();
    translate(this.x, this.y);
    rotate(this.angle);
    
    let c = color(red(this.color), green(this.color), blue(this.color), max(0, this.life * 255));
    fill(c);
    noStroke();
    
    let curSize = max(0, this.size * this.life);
    if (this.type === 0) {
      rectMode(CENTER);
      rect(0, 0, curSize, curSize, curSize * 0.2);
    } else if (this.type === 1) {
      triangle(-curSize/2, curSize/2, 0, -curSize/2, curSize/2, curSize/2);
    } else {
      // 繪製精緻十字星
      rectMode(CENTER);
      rect(0, 0, curSize, curSize * 0.2);
      rect(0, 0, curSize * 0.2, curSize);
    }
    pop();
  }

  isDead() {
    return this.life <= 0;
  }
}

/**
 * 更新並繪製所有軌跡粒子
 */
function updateAndDrawParticles() {
  for (let i = particles.length - 1; i >= 0; i--) {
    particles[i].update();
    particles[i].display();
    if (particles[i].isDead()) {
      particles.splice(i, 1);
    }
  }
}

/**
 * 點擊波紋類別 (Ripple)
 */
class Ripple {
  constructor(x, y) {
    this.x = x;
    this.y = y;
    this.radius = 0;
    this.maxRadius = max(width, height) * 0.6;
    this.speed = 8;
    this.life = 1.0;
  }

  update() {
    this.radius += this.speed;
    this.life = map(this.radius, 0, this.maxRadius, 1.0, 0.0, true);
  }

  display() {
    // 繪製多重同心波紋線，增添層次
    noFill();
    strokeWeight(1.5);
    for (let offset = 0; offset < 3; offset++) {
      let r = this.radius - offset * 25;
      if (r > 0) {
        let alpha = this.life * 150 * (1 - offset * 0.3);
        // 波紋採用柑橘黃或白色等高反差流行色
        let baseBg = interpolatedColors[3 % interpolatedColors.length];
        stroke(red(baseBg), green(baseBg), blue(baseBg), alpha);
        circle(this.x, this.y, r * 2);
      }
    }
  }

  // 計算該波紋在某距離下的受力影響 (用於變形蟲與貝茲曲線扭曲)
  getForceAtDistance(d) {
    let thickness = 80; // 波紋邊緣厚度
    let diff = abs(d - this.radius);
    if (diff < thickness) {
      // 越靠近波紋邊緣，正弦受力越大
      return sin(map(diff, 0, thickness, HALF_PI, 0)) * this.life;
    }
    return 0;
  }

  isDead() {
    return this.radius >= this.maxRadius;
  }
}


// ============================================================
// 7. HTML 介面與事件綁定 (毛玻璃面板控制器)
// ============================================================

/**
 * 綁定控制面板上的所有 DOM 交互事件
 */
function bindInterfaceEvents() {
  // 1. 藝術視覺模式切換
  const modeButtons = [
    document.getElementById("btn-mode-1"),
    document.getElementById("btn-mode-2"),
    document.getElementById("btn-mode-3")
  ];
  
  modeButtons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      // 移除其他按鈕的 active
      modeButtons.forEach(b => b.classList.remove("active"));
      // 設為 active
      btn.classList.add("active");
      // 更新 p5.js 模式
      visualMode = parseInt(btn.getAttribute("data-mode"));
    });
  });
  
  // 2. 調色盤切換
  const paletteSelector = document.getElementById("palette-selector");
  const palButtons = paletteSelector.querySelectorAll(".palette-btn");
  
  palButtons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      palButtons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      
      currentPaletteIndex = parseInt(btn.getAttribute("data-palette"));
      // 更新目標配色 (將會平滑過渡)
      updatePaletteTarget(currentPaletteIndex);
    });
  });
  
  // 3. 參數滑桿綁定
  const sliderDensity = document.getElementById("slider-density");
  const valDensity = document.getElementById("val-density");
  sliderDensity.addEventListener("input", (e) => {
    densityVal = parseInt(e.target.value);
    valDensity.innerText = densityVal;
  });

  const sliderSpeed = document.getElementById("slider-speed");
  const valSpeed = document.getElementById("val-speed");
  sliderSpeed.addEventListener("input", (e) => {
    speedVal = parseFloat(e.target.value);
    valSpeed.innerText = speedVal.toFixed(1);
  });

  const sliderSensitivity = document.getElementById("slider-sensitivity");
  const valSensitivity = document.getElementById("val-sensitivity");
  sliderSensitivity.addEventListener("input", (e) => {
    sensitivityVal = parseFloat(e.target.value);
    valSensitivity.innerText = sensitivityVal.toFixed(1);
  });

  // 4. 自動流動開關
  const toggleAutoplay = document.getElementById("toggle-autoplay");
  toggleAutoplay.addEventListener("change", (e) => {
    autoFlow = e.target.checked;
  });

  // 5. 隨機重組按鈕
  const btnRandom = document.getElementById("btn-random");
  btnRandom.addEventListener("click", () => {
    // 隨機選一個流行色調色盤
    let randPalIdx = floor(random(COLOR_PALETTES.length));
    updatePaletteTarget(randPalIdx);
    
    // 更新 UI active 狀態
    palButtons.forEach(b => {
      if (parseInt(b.getAttribute("data-palette")) === randPalIdx) {
        b.classList.add("active");
      } else {
        b.classList.remove("active");
      }
    });

    // 隨機混搭一部分顏色
    for (let i = 0; i < targetPaletteColors.length; i++) {
      if (random() < 0.5) {
        let randCol = color(random(COLOR_PALETTES[floor(random(COLOR_PALETTES.length))].colors));
        targetPaletteColors[i] = randCol;
      }
    }
    
    // 重新渲染色塊預覽
    renderPalettePreview();
    
    // 隨機改變滑桿數值
    let randDensity = floor(random(6, 20));
    sliderDensity.value = randDensity;
    densityVal = randDensity;
    valDensity.innerText = randDensity;
    
    let randSpeed = parseFloat(random(0.5, 2.0).toFixed(1));
    sliderSpeed.value = randSpeed;
    speedVal = randSpeed;
    valSpeed.innerText = randSpeed;

    // 觸發隨機模式
    let randMode = floor(random(1, 4));
    visualMode = randMode;
    modeButtons.forEach(b => {
      if (parseInt(b.getAttribute("data-mode")) === randMode) {
        b.classList.add("active");
      } else {
        b.classList.remove("active");
      }
    });
  });

  // 6. 導出藝術圖按鈕
  const btnExport = document.getElementById("btn-export");
  btnExport.addEventListener("click", () => {
    // 短暫隱藏控制面板以取得乾淨的圖檔 (雖然 p5.js 本來就不會錄製 HTML 元素，但為了保險)
    saveCanvas(`2026-geometric-art-${visualMode}`, 'png');
    showToast("藝術圖已成功導出！");
  });
}

/**
 * 當切換調色盤時，更新色彩過渡目標
 * @param {number} index 調色盤索引
 */
function updatePaletteTarget(index) {
  let palette = COLOR_PALETTES[index].colors;
  for (let i = 0; i < palette.length; i++) {
    targetPaletteColors[i] = color(palette[i]);
  }
  // 延遲更新色塊預覽 (等過渡完成或直接更新)
  setTimeout(renderPalettePreview, 100);
}

/**
 * 動態渲染當前調色盤的色塊 preview 區，並點擊複製 HEX 色碼
 */
function renderPalettePreview() {
  const previewContainer = document.getElementById("palette-preview");
  if (!previewContainer) return;
  
  // 清空
  previewContainer.innerHTML = "";
  
  // 使用當前目標配色色彩 (保持最新)
  let hexColors = COLOR_PALETTES[currentPaletteIndex].colors;
  
  hexColors.forEach(hex => {
    const block = document.createElement("div");
    block.className = "color-block";
    block.style.backgroundColor = hex;
    
    const hexSpan = document.createElement("span");
    hexSpan.className = "color-hex";
    hexSpan.innerText = hex;
    
    block.appendChild(hexSpan);
    
    // 點擊事件：複製色碼並提示
    block.addEventListener("click", () => {
      navigator.clipboard.writeText(hex)
        .then(() => {
          showToast(`已複製色碼 ${hex} 到剪貼簿！`);
        })
        .catch(err => {
          console.error("無法複製色碼：", err);
        });
    });
    
    previewContainer.appendChild(block);
  });
}

/**
 * 顯示 Toast 提示視窗
 * @param {string} msg 提示訊息
 */
function showToast(msg) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  
  toast.innerText = msg;
  toast.classList.add("show");
  
  // 2.5秒後淡出
  setTimeout(() => {
    toast.classList.remove("show");
  }, 2500);
}
