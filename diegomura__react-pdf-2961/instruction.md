# Tspan inside if Text Element is broken

**Describe the bug**
When I place multiple `Tspan` elements inside of a `Text` element, the SVG is not rendered correctly.
The `Tspan` elements are rendered on top of each other.

**To Reproduce**
```javascript
const BrokenTspan = () => (
  <Document>
    <Page>
      <Svg>
        <Text x="10" y="30">
          <Tspan>test</Tspan>
          <Tspan style={{ fill: "red" }}>test</Tspan>
          <Tspan style={{ fill: "black" }}>1234</Tspan>
        </Text>
      </Svg>
    </Page>
  </Document>
);

ReactPDF.render(<BrokenTspan />);
```
![image](/testbed/assets/diegomura__react-pdf-2961/issue-01.png)

Reproducible example in REPL: /testbed/assets/diegomura__react-pdf-2961/reproduce.cjs

**Expected behavior**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">
<text x="10" y="30">
  <tspan>test</tspan>
  <tspan fill="red">test</tspan>
  <tspan fill="black">1234</tspan>
</text>
</svg>
```

![image](/testbed/assets/diegomura__react-pdf-2961/issue-02.png)


**Desktop (please complete the following information):**

- OS: MacOS
- Browser: chrome
- React-pdf version: 4.0.0


离线复现：运行 `node /testbed/assets/diegomura__react-pdf-2961/reproduce.cjs`，查看 `/testbed/.reproduction/` 中的输出。复现脚本使用当前源码，不包含修复或判分断言。

资源：`/testbed/assets/diegomura__react-pdf-2961/reproduce.cjs`

请在 `/testbed` 中修改代码，修复上述问题并保持既有行为。
