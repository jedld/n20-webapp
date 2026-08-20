import { describe, expect, it } from 'vitest';
import { normalizeWallStyle, wallSpecFromState } from './materials.js';

describe('wall styles', () => {
  it('aliases haunted manor looks to gothic', () => {
    expect(normalizeWallStyle('gothic')).toBe('gothic');
    expect(normalizeWallStyle('haunted')).toBe('gothic');
    expect(normalizeWallStyle('victorian')).toBe('gothic');
    expect(normalizeWallStyle('gothic_mansion')).toBe('gothic');
    expect(normalizeWallStyle('mansion')).toBe('mansion');
    expect(normalizeWallStyle('office')).toBe('office');
    expect(normalizeWallStyle('modern')).toBe('office');
    expect(normalizeWallStyle('corporate')).toBe('office');
    expect(normalizeWallStyle('startup')).toBe('office');
    expect(normalizeWallStyle('cave')).toBe('cave');
    expect(normalizeWallStyle('cavern')).toBe('cave');
    expect(normalizeWallStyle('grotto')).toBe('cave');
    expect(normalizeWallStyle('dungeon')).toBe('dungeon');
  });

  it('reads gothic from map state', () => {
    expect(wallSpecFromState({ map: { walls: { style: 'gothic' } } }).style).toBe('gothic');
  });

  it('reads cave from map state', () => {
    expect(wallSpecFromState({ map: { walls: { style: 'cave' } } }).style).toBe('cave');
    expect(wallSpecFromState({ map: { walls: { style: 'cavern' } } }).style).toBe('cave');
  });
});
