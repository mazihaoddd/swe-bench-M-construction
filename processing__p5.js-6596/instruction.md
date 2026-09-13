# Inconsistency in lights() and specularmaterial() within Framebuffer Block when filter() is Applied inside the block

### Most appropriate sub-area of p5.js?

- [ ] Accessibility
- [ ] Color
- [ ] Core/Environment/Rendering
- [ ] Data
- [ ] DOM
- [ ] Events
- [ ] Image
- [ ] IO
- [ ] Math
- [ ] Typography
- [ ] Utilities
- [X] WebGL
- [ ] Build Process
- [ ] Unit Testing
- [ ] Internalization
- [ ] Friendly Errors
- [ ] Other (specify if possible)

### p5.js version

v1.9.0

### Web browser and version

Chrome

### Operating System

Windows

### Steps to reproduce this

### Steps:
1. Create a scene with lighting and materials.
2. Use the framebuffer block to apply a filter to the scene.
3. Observe that the lights() and specularmaterial() functions do not behave as expected within the framebuffer block when filter is applied.

![fixes-blur](/testbed/assets/processing__p5.js-6596/issue-01.png)



请在 `/testbed` 中修改代码，修复上述问题并保持既有行为。
